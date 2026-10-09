import threading
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.client.ports import MarketDataPort, TradingPort
from smart_trader.exceptions import SmartTraderError
from smart_trader.execution.journaled_trading import JournaledTradingPort
from smart_trader.execution.limit_order_chaser import LimitOrderChaser
from smart_trader.execution.order_reconciler import OrderReconciler
from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord
from smart_trader.strategy import Signal, SingleTimeframeStrategy

logger = log.getLogger(__name__)

# K 线周期 → 秒数; 孤儿回收阈值取 max(600, 周期时长)
_TIMEFRAME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_ORPHAN_MIN_AGE_SECONDS = 600


def _timeframe_seconds(timeframe: str) -> int:
    """把 "1m" / "1h" / "1d" 之类的周期解析成秒数"""
    unit = timeframe[-1:].lower()
    value = timeframe[:-1]
    if unit not in _TIMEFRAME_UNITS or not value.isdigit():
        raise ValueError(f"无法解析 K 线周期: {timeframe}")
    return int(value) * _TIMEFRAME_UNITS[unit]


class GridPosition(BaseModel):
    """逻辑仓位: 只持策略参数与运行时状态, 价格 / 数量 / 相位由 orders 派生"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    strategy_key: str
    symbol: str
    position_side: PositionSide
    main_order_side: OrderSide
    opening_quantity: float = 0.0
    opening_price: float = 0.0
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    enable_stop_loss: bool = False
    stop_loss_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.0
    trailing_stop_activation_rate: float = 0.0
    trailing_stop_price: float | None = None
    active: bool = True
    created_at: str = ""
    updated_at: str = ""
    orders: list[OrderRecord] = []

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other: object):
        if isinstance(other, GridPosition):
            return self.id == other.id
        return False

    # ---- 入/出场归类（按 position_side） ----

    @property
    def entry_side(self) -> OrderSide:
        return OrderSide.BUY if self.position_side == PositionSide.LONG else OrderSide.SELL

    @property
    def exit_side(self) -> OrderSide:
        return self.entry_side.reversal()

    @property
    def entry_orders(self) -> list[OrderRecord]:
        return [order for order in self.orders if order.side == self.entry_side]

    @property
    def exit_orders(self) -> list[OrderRecord]:
        return [order for order in self.orders if order.side == self.exit_side]

    # ---- 派生量（不落库） ----

    @property
    def entry_filled(self) -> float:
        return sum(order.filled_qty for order in self.entry_orders)

    @property
    def exit_filled(self) -> float:
        return sum(order.filled_qty for order in self.exit_orders)

    @property
    def quantity(self) -> float:
        return self.opening_quantity + self.entry_filled - self.exit_filled

    @property
    def price(self) -> float:
        """
        加权均价: (opening_price×opening_quantity, entry 行成交价×成交量)

        尚无成交（basis 为 0）时退回最近一条入场单的委托价 —— 未成交的入场单
        以委托价作为网格参考价, 否则空仓位的网格间隔 / 止盈判定会失去基准。
        """
        basis_qty = self.opening_quantity
        notional = self.opening_price * self.opening_quantity
        for order in self.entry_orders:
            if order.filled_qty <= 0:
                continue
            unit = order.avg_price if order.avg_price is not None else order.price
            if unit is None:
                continue
            notional += unit * order.filled_qty
            basis_qty += order.filled_qty
        if basis_qty <= 0:
            for order in reversed(self.entry_orders):
                if order.price is not None:
                    return order.price
            return self.opening_price
        return notional / basis_qty

    @property
    def pending_entry_orders(self) -> list[OrderRecord]:
        """仍未了结的入场单（status 为空或 OPEN）"""
        return [
            order
            for order in self.orders
            if order.side == self.entry_side
            and (order.status is None or order.status.is_open())
        ]

    @property
    def pending_exit_order(self) -> OrderRecord | None:
        """未成交的出场单（status 为空或 OPEN）; 每仓位至多一张"""
        for order in self.orders:
            if order.side == self.exit_side and (
                order.status is None or order.status.is_open()
            ):
                return order
        return None

    @property
    def has_pending_exit(self) -> bool:
        return self.pending_exit_order is not None

    @property
    def has_live_orders(self) -> bool:
        """是否存在任何存活订单（status 为空或 OPEN）"""
        return any(
            order.status is None or order.status.is_open() for order in self.orders
        )

    @property
    def phase(self) -> str:
        if not self.active:
            return "closed"
        if self.quantity <= 0:
            return "opening"
        if self.has_pending_exit:
            return "closing"
        return "holding"

    def profit_level(self, current_price: float) -> int:
        """
        计算逻辑仓位的盈利级别
        @param current_price 当前价格
        @return 表示盈利级别
            -1亏损中（或均价不可用）
            0盈利无法覆盖手续费
            1盈利中
            2达到止盈标准
        """
        if self.price <= 0:
            return -1
        compare_fun = self.entry_side.compare_fun()

        if compare_fun(current_price, self._profit(self.fixed_take_profit_rate)):
            return 2
        elif compare_fun(current_price, self._profit(self.signal_min_take_profit_rate)):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    def profit_and_loss_ratio(self, current_price: float) -> float:
        if self.price <= 0:
            return 0.0
        loss_rate = float(f"{abs(current_price - self.price) / self.price:.6f}")
        if self.profit_level(current_price) < 0:
            return -loss_rate
        return loss_rate

    def _profit(self, rate: float) -> float:
        rate_base = -1 if self.entry_side == OrderSide.SELL else 1
        return self.price * (1 + rate * rate_base)

    def to_row(self) -> GridPositionRow:
        """转换为账本行（不含 orders）"""
        return GridPositionRow(
            id=self.id,
            strategy_key=self.strategy_key,
            symbol=self.symbol,
            position_side=self.position_side,
            main_order_side=self.main_order_side,
            opening_quantity=self.opening_quantity,
            opening_price=self.opening_price,
            fixed_take_profit_rate=self.fixed_take_profit_rate,
            signal_min_take_profit_rate=self.signal_min_take_profit_rate,
            enable_stop_loss=self.enable_stop_loss,
            stop_loss_rate=self.stop_loss_rate,
            enable_trailing_stop=self.enable_trailing_stop,
            trailing_stop_rate=self.trailing_stop_rate,
            trailing_stop_activation_rate=self.trailing_stop_activation_rate,
            trailing_stop_price=self.trailing_stop_price,
            active=self.active,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_row(cls, row: GridPositionRow, orders: list[OrderRecord]) -> "GridPosition":
        """从账本行 + 关联订单还原"""
        return cls(
            id=row.id,
            strategy_key=row.strategy_key,
            symbol=row.symbol,
            position_side=row.position_side,
            main_order_side=row.main_order_side,
            opening_quantity=row.opening_quantity,
            opening_price=row.opening_price,
            fixed_take_profit_rate=row.fixed_take_profit_rate,
            signal_min_take_profit_rate=row.signal_min_take_profit_rate,
            enable_stop_loss=row.enable_stop_loss,
            stop_loss_rate=row.stop_loss_rate,
            enable_trailing_stop=row.enable_trailing_stop,
            trailing_stop_rate=row.trailing_stop_rate,
            trailing_stop_activation_rate=row.trailing_stop_activation_rate,
            trailing_stop_price=row.trailing_stop_price,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            orders=list(orders),
        )


class GridPositionManager:
    """线程安全的逻辑仓位管理器（内存镜像账本; 写账在动作发生处由策略调用）"""

    def __init__(self, journal: OrderJournal, strategy_key: str):
        self._positions: dict[str, GridPosition] = {}
        self._lock = threading.RLock()
        self._journal = journal
        self._strategy_key = strategy_key

    @property
    def positions(self) -> list[GridPosition]:
        """获取逻辑仓位列表的线程安全副本"""
        with self._lock:
            return list(self._positions.values())

    def add_position(self, position: GridPosition) -> None:
        """添加逻辑仓位"""
        with self._lock:
            self._positions[position.id] = position

    def remove_positions(self, positions: list[GridPosition]) -> None:
        """批量移出已归档的逻辑仓位, 防止同一根 K 线内被重复处理"""
        with self._lock:
            for position in positions:
                self._positions.pop(position.id, None)

    def load_positions(self, force: bool = False) -> bool:
        """
        从账本加载逻辑仓位（含关联订单, 覆盖内存）
        @param force 强制重读; 运行期内存是权威状态, 只有启动时必须加载
        """
        if not force:
            return False

        with self._lock:
            rows = self._journal.load_active_positions(self._strategy_key)
            orders = self._journal.load_orders_for_positions([row.id for row in rows])
            by_position: dict[str, list[OrderRecord]] = {}
            for order in orders:
                by_position.setdefault(order.grid_position_id, []).append(order)
            logger.info("从账本加载逻辑仓位: %s 笔", len(rows))
            for row in rows:
                position = GridPosition.from_row(row, by_position.get(row.id, []))
                self._positions[position.id] = position
            return True


class SignalGridStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: Symbol
    timeframe: str
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    grid_spacing_rate: float = 0.01
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    signal: Signal | None = None

    # 启用退出信号
    enable_exit_signal: bool = True
    # 退出信号最小止盈率
    exit_signal_take_profit_min_rate: float = 0.002

    # 固定比例止盈
    fixed_rate_take_profit: bool = False
    # 当fixed_rate_take_profit为True时, 是否使用限价单止盈
    # 开启会在入场订单完成之后创建止盈订单(默认情况下只有K线的收盘价达到止盈标准时才会触发止盈)
    take_profit_use_limit_order: bool = False
    # 固定比例止盈率
    fixed_take_profit_rate: float = 0.006

    place_order_behavior: PlaceOrderBehavior = (
        PlaceOrderBehavior.CHASER_OPEN
    )  # 下单行为

    # 账本归属键: 多策略共享一个数据库, 按此键存取订单
    strategy_key: str

    position_reverse: bool = False  # 是否反向持仓
    # 达到最大订单数全部止损
    enable_max_order_stop_loss: bool = False
    # 止损后暂停策略
    paused_after_stop_loss: bool = True
    # 单笔订单止损
    enable_order_stop_loss: bool = False
    order_stop_loss_rate: float = 0.05
    # 跟踪止损
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.02
    trailing_stop_activation_profit_rate: float = 0.01


class SignalGridStrategy(SingleTimeframeStrategy):
    def __init__(
        self,
        config: SignalGridStrategyConfig,
        trading: TradingPort,
        market: MarketDataPort,
        journal: OrderJournal,
        reconcile_min_age_seconds: float = 5.0,
    ):
        super().__init__(config.timeframe, market)
        self.config = config
        self.trading = trading
        self.journal = journal

        # 启动对账: 先用订单行确认遗留的未完成下单, 再从账本恢复仓位
        self.reconciler = OrderReconciler(
            journal=journal,
            trading=trading,
            strategy_key=config.strategy_key,
            min_age_seconds=reconcile_min_age_seconds,
        )
        self.reconciler.reconcile()
        self.position_manager = GridPositionManager(
            journal=journal, strategy_key=config.strategy_key
        )
        self.position_manager.load_positions(force=True)

        self.on_stop_loss_order_all: Callable[[], None] = lambda: None
        self.close_position: bool = False
        self.is_running: bool = True

    # ---- 方向推导 ----

    def _position_side(self) -> PositionSide:
        side = self.config.position_side
        if self.config.position_reverse:
            return (
                PositionSide.SHORT if side == PositionSide.LONG else PositionSide.LONG
            )
        return side

    def _entry_side(self) -> OrderSide:
        side = self.config.master_side
        if self.config.position_reverse:
            return side.reversal()
        return side

    def place_order(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        first_price: float | None = None,
        position: GridPositionRow | None = None,
        grid_position_id: str | None = None,
    ) -> OrderResult | None:
        """
        下单入口: 订单号由账本在发送前分发, 记账下单保证交易所与账本最终一致
        @param side: 买卖方向(已做反向处理)
        @param qty: 数量
        @param price: 价格
        @param first_price: 追单第一档价格
        @param position: 新仓位的策略参数模板（首单同事务建仓位）
        @param grid_position_id: 追加订单到已有仓位
        @return: 交易所返回的订单结果
        """
        request = OrderRequest(
            custom_id="",  # 订单号由账本在发送前分发, 保证先登记后下单
            symbol=self.config.symbol,
            side=side,
            type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            position_side=self._position_side(),
        )
        placer = JournaledTradingPort(
            trading=self.trading,
            journal=self.journal,
            strategy_key=self.config.strategy_key,
            position=position,
            grid_position_id=grid_position_id,
        )

        if self.config.place_order_behavior == PlaceOrderBehavior.NORMAL:
            return placer.place(request)

        chaser = LimitOrderChaser(
            trading=placer,
            market=self.market,
            symbol=self.config.symbol,
            side=side,
            quantity=qty,
            position_side=self._position_side(),
            behavior=self.config.place_order_behavior,
        )
        result = chaser.run(price, first_price=first_price)
        if result is not None:
            return result

        # 追单耗尽仍未挂上: 降级为常规限价单, 避免静默丢单
        logger.warning(
            "追单未成功, 降级为常规限价单: %s, %s, Qty: %s",
            self.config.symbol.ccxt(),
            side.name,
            qty,
        )
        return placer.place(request)

    def _order_record(
        self,
        position: GridPosition,
        side: OrderSide,
        qty: float,
        price: float,
        result: OrderResult,
    ) -> OrderRecord:
        """构造与账本预写行内容一致的内存订单记录"""
        return OrderRecord(
            order_id=result.custom_id,
            grid_position_id=position.id,
            strategy_key=self.config.strategy_key,
            symbol=self.config.symbol.ccxt(),
            side=result.side if result.side is not None else side,
            type=OrderType.LIMIT,
            price=result.price if result.price is not None else price,
            quantity=qty,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
            status=result.status,
        )

    def _check_max_order_stop_loss(self) -> bool:
        return (
            self.config.enable_max_order_stop_loss
            and self.config.max_order - len(self.position_manager.positions) <= 1
        )

    def check_open_position(self) -> bool:
        # 检查订单是否到达上限
        if len(self.position_manager.positions) >= self.config.max_order:
            return False

        # 检查是否有入场信号
        if self.config.signal and not self.config.signal.is_entry(self.klines_df):
            return False

        # 检查当前价格是否在可交易的价格区间
        if self.latest_kline_obj is None:
            return False

        close_price = self.latest_kline_obj.close
        if (
            close_price < self.config.lowest_price
            or close_price > self.config.highest_price
        ):
            return False

        if self.position_manager.positions:
            recent_price_position = self.config.master_side.extremum_fun()(
                self.position_manager.positions, key=lambda position: position.price
            )
        else:
            recent_price_position = None

        if (not recent_price_position) or (
            recent_price_position
            and recent_price_position.profit_and_loss_ratio(close_price)
            <= -self.config.grid_spacing_rate
        ):
            if self._check_max_order_stop_loss():
                return False
            stop_loss_rate = (
                self.config.order_stop_loss_rate
                if self.config.enable_order_stop_loss
                else 0.0
            )
            trailing_stop_rate = (
                self.config.trailing_stop_rate
                if self.config.enable_trailing_stop
                else 0.0
            )
            trailing_activation_rate = (
                self.config.trailing_stop_activation_profit_rate
                if self.config.enable_trailing_stop
                else 0.0
            )

            trailing_stop_price = None
            if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
                if self._entry_side() == OrderSide.BUY:
                    trailing_stop_price = close_price * (1 - stop_loss_rate)
                else:  # SELL
                    trailing_stop_price = close_price * (1 + stop_loss_rate)

            template = GridPositionRow(
                id=self.journal.generate_position_id(),
                strategy_key=self.config.strategy_key,
                symbol=self.config.symbol.ccxt(),
                position_side=self._position_side(),
                main_order_side=self.config.master_side,
                # 开仓参考价: 无成交（如 per_order_qty=0 的空仓位或首单未成交）时
                # 作为网格间隔 / 止盈判定的基准; created_at 用于孤儿超时回收计时
                opening_price=close_price,
                created_at=datetime.now(UTC).isoformat(),
                fixed_take_profit_rate=self.config.fixed_take_profit_rate,
                signal_min_take_profit_rate=(
                    self.config.exit_signal_take_profit_min_rate
                ),
                enable_stop_loss=self.config.enable_order_stop_loss,
                stop_loss_rate=stop_loss_rate,
                enable_trailing_stop=self.config.enable_trailing_stop,
                trailing_stop_rate=trailing_stop_rate,
                trailing_stop_activation_rate=trailing_activation_rate,
                trailing_stop_price=trailing_stop_price,
            )

            if self.config.per_order_qty == 0:
                # 零数量: 只登记一个空仓位（无首单）
                self.journal.upsert_position(template)
                self.position_manager.add_position(GridPosition.from_row(template, []))
                return True

            entry_result = self.place_order(
                self._entry_side(),
                self.config.per_order_qty,
                close_price,
                first_price=close_price,
                position=template,
            )
            position = GridPosition.from_row(template, [])
            if entry_result is not None:
                position.orders.append(
                    self._order_record(
                        position,
                        self._entry_side(),
                        self.config.per_order_qty,
                        close_price,
                        entry_result,
                    )
                )
            self.position_manager.add_position(position)
            return True
        return False

    # ---- 出场生命周期 ----

    def _settle_order(
        self, position: GridPosition, order: OrderRecord, result: OrderResult
    ) -> None:
        """用交易所事实回写一条订单行（账本 + 内存）"""
        updated = order.model_copy(
            update={
                "price": result.price if result.price is not None else order.price,
                "quantity": (
                    result.quantity if result.quantity > 0 else order.quantity
                ),
                "filled_qty": result.filled_qty,
                "avg_price": result.avg_price,
                "status": result.status,
            }
        )
        self.journal.settle_order(updated)
        position.orders = [
            updated if item.order_id == updated.order_id else item
            for item in position.orders
        ]

    def _discard_order(self, position: GridPosition, order_id: str) -> None:
        """交易所从未存在该订单: 删行并从内存移除"""
        self.journal.discard_order(order_id)
        position.orders = [item for item in position.orders if item.order_id != order_id]

    def _cancel_and_refresh(self, custom_id: str) -> OrderResult | None:
        """
        撤掉未成交完的挂单并复查终态

        撤单请求发出前可能又有成交, 因此复查一次拿最终的成交量与状态;
        复查失败(返回 None)时由调用方沿用已知状态。
        """
        self.trading.cancel(custom_id, self.config.symbol)
        return self.trading.query(custom_id, self.config.symbol)

    def _refresh_pending_entries(self, position: GridPosition) -> None:
        """查询并了结未成交的入场单, 防止继续建仓"""
        for order in list(position.pending_entry_orders):
            result = self.trading.query(order.order_id, self.config.symbol)
            if result is None:
                self._settle_order(
                    position,
                    order,
                    OrderResult(
                        custom_id=order.order_id,
                        symbol=self.config.symbol,
                        side=order.side,
                        status=OrderStatus.EXPIRED,
                        quantity=order.quantity,
                        filled_qty=order.filled_qty,
                        price=order.price,
                    ),
                )
                continue
            self._settle_order(position, order, result)
            if result.status.is_open():
                refreshed = self._cancel_and_refresh(order.order_id)
                if refreshed is not None:
                    self._settle_order(position, order, refreshed)

    def _force_settle_exit(self, position: GridPosition) -> bool:
        """强制出场: 撤掉休息中的出场挂单并结算已成交部分, 返回是否仍有可平量"""
        order = position.pending_exit_order
        if order is None:
            return position.quantity > 0
        refreshed = self._cancel_and_refresh(order.order_id)
        if refreshed is None:
            self._discard_order(position, order.order_id)
        else:
            self._settle_order(position, order, refreshed)
        return position.quantity > 0

    def _track_pending_exits(self) -> tuple[list[GridPosition], bool]:
        """
        每根完成K线无条件跟踪已挂出的出场单

        - 未成交(open): 保留原挂单, 继续跟踪;
        - 确认成交(closed): 结算成交量, 剩余耗尽才算平仓完成;
        - 作废(canceled/rejected/expired): 结算后退回持仓, 等待重挂;
        - 查询不到: 先撤单复核, 确认订单不存在才删行退回持仓;
        - 查询异常: 保留出场单, 下一根再试, 绝不重复下单。

        @return: (可归档的仓位, 状态是否发生变化)
        """
        archived: list[GridPosition] = []
        changed = False
        for position in self.position_manager.positions:
            order = position.pending_exit_order
            if order is None:
                continue

            try:
                result = self.trading.query(order.order_id, self.config.symbol)
            except SmartTraderError:
                logger.warning("查询出场单失败, 保留待跟踪: %s", order.order_id)
                continue

            if result is None:
                # 订单可能只是新单索引延迟, 撤单复核后再判定是否存在
                result = self._cancel_and_refresh(order.order_id)
                if result is None:
                    logger.warning("出场单不存在, 退回持仓: %s", order.order_id)
                    self._discard_order(position, order.order_id)
                    changed = True
                    continue

            if result.status.is_open():
                # 保留原挂单, 不需要重复挂
                continue

            self._settle_order(position, order, result)
            changed = True
            if position.quantity <= 0:
                archived.append(position)

        return archived, changed

    def _stop_loss_triggered(self, position: GridPosition, close: float) -> bool:
        if not position.enable_stop_loss or position.trailing_stop_price is None:
            return False
        if position.entry_side == OrderSide.BUY:
            return close <= position.trailing_stop_price
        return close >= position.trailing_stop_price

    def check_close_position(self) -> list[GridPosition]:
        """
        触发平仓条件, 为每仓位挂出独立的出场单

        - 每仓位一张出场单, 成交可独立归因;
        - 已有未成交出场单的仓位: 普通止盈保留原挂单, 强制出场先撤单结算再重挂;
        - 只有真正完成的仓位(零成交入场单丢弃 / 出场剩余耗尽)才返回归档。
        """
        if self.latest_kline_obj is None:
            return []

        close = self.latest_kline_obj.close
        exit_signal = (
            self.config.enable_exit_signal
            and self.config.signal
            and self.config.signal.is_exit(self.klines_df)
        )

        archived: list[GridPosition] = []
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for position in self.position_manager.positions:
            profit_level = position.profit_level(close)
            force_exit = stop_loss_order_all or self._stop_loss_triggered(
                position, close
            )
            normal_exit = (profit_level == 2 and self.config.fixed_rate_take_profit) or (
                profit_level == 1 and exit_signal
            )

            # 已有未成交的出场单: 普通止盈保留原挂单; 强制出场才撤单结算后重挂
            if position.has_pending_exit:
                if not force_exit:
                    continue
                if not self._force_settle_exit(position):
                    archived.append(position)
                    continue

            if not (force_exit or normal_exit):
                continue

            # 未成交入场单: 先撤剩余挂单防止继续建仓, 零成交则无可平仓位
            self._refresh_pending_entries(position)
            if position.quantity <= 0:
                archived.append(position)
                continue

            exit_qty = position.quantity
            exit_price = close
            exit_result = self.place_order(
                position.exit_side,
                exit_qty,
                exit_price,
                grid_position_id=position.id,
            )
            if exit_result is not None:
                position.orders.append(
                    self._order_record(
                        position, position.exit_side, exit_qty, exit_price, exit_result
                    )
                )

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return archived

    # ---- 每根完成K线的维护 ----

    def _update_trailing_stop(self) -> None:
        kline = self.latest_kline_obj
        if kline is None:
            return
        extremum_price = (
            kline.high if self._entry_side() == OrderSide.BUY else kline.low
        )
        for position in self.position_manager.positions:
            if not position.enable_trailing_stop or position.trailing_stop_price is None:
                continue

            activation_price = position.price * (
                1
                + position.trailing_stop_activation_rate * position.entry_side.to_int()
            )
            if position.entry_side.compare_fun(and_eq=True)(
                extremum_price, activation_price
            ):
                new_stop_price = extremum_price * (
                    1 - position.trailing_stop_rate * position.entry_side.to_int()
                )
                position.trailing_stop_price = position.entry_side.reversal().extremum_fun()(
                    position.trailing_stop_price, new_stop_price
                )
                self.journal.upsert_position(position.to_row())

    def _reap_orphans(self) -> list[GridPosition]:
        """
        孤儿回收: active=1 且 quantity<=0 且无任何存活订单, 且存在时长超过
        max(10 分钟, 策略 K 线周期时长) 的空仓位才回收（置 active=0）。
        """
        threshold = max(_ORPHAN_MIN_AGE_SECONDS, _timeframe_seconds(self.config.timeframe))
        archived: list[GridPosition] = []
        for position in self.position_manager.positions:
            if position.quantity > 0 or position.has_live_orders:
                continue
            if self._orphan_expired(position, threshold):
                archived.append(position)
        return archived

    @staticmethod
    def _orphan_expired(position: GridPosition, threshold_seconds: int) -> bool:
        try:
            created = datetime.fromisoformat(position.created_at)
        except ValueError:
            return False
        age = (datetime.now(UTC) - created).total_seconds()
        return age > threshold_seconds

    def _archive(self, positions: list[GridPosition]) -> None:
        for position in positions:
            self.journal.close_position(position.id)
        self.position_manager.remove_positions(positions)

    def _on_kline_finished(self):
        if self.latest_kline_obj is None:
            return

        if self.is_running:
            self._update_trailing_stop()

        # 无条件跟踪已挂出的出场单(暂停后仍必须跟踪)
        archived, _ = self._track_pending_exits()

        if self.is_running and not self.check_open_position():
            archived.extend(self.check_close_position())

        # 孤儿回收（每根完成K线）
        archived.extend(self._reap_orphans())

        self._archive(archived)

    def _on_kline(self):
        if self.latest_kline_obj is None:
            return
        if not (
            self.config.fixed_rate_take_profit
            and self.config.take_profit_use_limit_order
        ):
            return

        for position in self.position_manager.positions:
            if position.quantity <= 0 and not position.pending_entry_orders:
                continue

            pending_exit = position.pending_exit_order
            if pending_exit is not None:
                exit_price = pending_exit.price
                if (
                    exit_price is not None
                    and self.latest_kline_obj.low
                    <= exit_price
                    <= self.latest_kline_obj.high
                ):
                    result = self.trading.query(pending_exit.order_id, self.config.symbol)
                    if result is not None and result.status.is_closed():
                        self._settle_order(position, pending_exit, result)
                        if position.quantity <= 0:
                            self._archive([position])
                continue

            self._refresh_pending_entries(position)
            if position.quantity > 0 and not position.pending_entry_orders:
                exit_price = position.price * (
                    1
                    + self._entry_side().to_int() * self.config.fixed_take_profit_rate
                )
                exit_qty = position.quantity
                exit_result = self.place_order(
                    position.exit_side,
                    exit_qty,
                    exit_price,
                    first_price=exit_price,
                    grid_position_id=position.id,
                )
                if exit_result is not None:
                    position.orders.append(
                        self._order_record(
                            position,
                            position.exit_side,
                            exit_qty,
                            exit_price,
                            exit_result,
                        )
                    )