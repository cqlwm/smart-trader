import secrets
import threading
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from smart_trader import log
from smart_trader.client.ports import MarketDataPort, TradingPort
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
from smart_trader.storage.records import GridPositionRow, OrderIntent, OrderRole
from smart_trader.strategy import Signal, SingleTimeframeStrategy

logger = log.getLogger(__name__)


def build_order_id(side: OrderSide):
    return f"{side.value}{secrets.token_hex(nbytes=5)}"


class GridPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    side: OrderSide
    price: float
    quantity: float
    # 入场单实际成交量: 平仓只能按实际成交量下, 否则部分成交时会超卖
    filled_quantity: float = 0.0
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    exit_price: float | None = None
    status: OrderStatus | None = None
    exit_id: str | None = None

    # Stop loss fields
    stop_loss_rate: float = 0.0
    enable_stop_loss: bool = False
    trailing_stop_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_activation_profit_rate: float = 0.0
    current_stop_price: float | None = None

    def __hash__(self):
        return hash(self.entry_id)

    def __eq__(self, other: object):
        if isinstance(other, GridPosition):
            return self.entry_id == other.entry_id
        return False

    @property
    def effective_filled_quantity(self) -> float:
        """
        平仓可用的实际数量。

        CCXT 会把"部分成交"映射为 open, 因此 closed 等价于完全成交;
        缺少成交量记录(如旧数据热加载)时按委托量兜底, 避免持仓被永久卡住。
        """
        if self.status is OrderStatus.CLOSED and not self.filled_quantity:
            return self.quantity
        return self.filled_quantity

    def profit_level(self, current_price: float) -> int:
        """
        计算逻辑仓位的盈利级别
        @param current_price 当前价格
        @return 表示盈利级别
            -1亏损中
            0盈利无法覆盖手续费
            1盈利中
            2达到止盈标准
        """
        compare_fun = self.side.compare_fun()

        if compare_fun(current_price, self._profit(self.fixed_take_profit_rate)):
            return 2
        elif compare_fun(current_price, self._profit(self.signal_min_take_profit_rate)):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    # 盈亏率
    def profit_and_loss_ratio(self, current_price: float) -> float:
        loss_rate = float(f"{abs(current_price - self.price) / self.price:.6f}")
        if self.profit_level(current_price) < 0:
            return -loss_rate
        else:
            return loss_rate

    def _profit(self, rate: float) -> float:
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)

    def to_row(self, strategy_key: str) -> GridPositionRow:
        """转换为账本行"""
        return GridPositionRow(
            entry_id=self.entry_id,
            strategy_key=strategy_key,
            side=self.side,
            price=self.price,
            quantity=self.quantity,
            filled_quantity=self.filled_quantity,
            fixed_take_profit_rate=self.fixed_take_profit_rate,
            signal_min_take_profit_rate=self.signal_min_take_profit_rate,
            exit_price=self.exit_price,
            status=self.status,
            exit_id=self.exit_id,
            stop_loss_rate=self.stop_loss_rate,
            enable_stop_loss=self.enable_stop_loss,
            trailing_stop_rate=self.trailing_stop_rate,
            enable_trailing_stop=self.enable_trailing_stop,
            trailing_stop_activation_profit_rate=(
                self.trailing_stop_activation_profit_rate
            ),
            current_stop_price=self.current_stop_price,
        )

    @classmethod
    def from_row(cls, row: GridPositionRow) -> "GridPosition":
        """从账本行还原"""
        return cls(
            entry_id=row.entry_id,
            side=row.side,
            price=row.price,
            quantity=row.quantity,
            filled_quantity=row.filled_quantity,
            fixed_take_profit_rate=row.fixed_take_profit_rate,
            signal_min_take_profit_rate=row.signal_min_take_profit_rate,
            exit_price=row.exit_price,
            status=row.status,
            exit_id=row.exit_id,
            stop_loss_rate=row.stop_loss_rate,
            enable_stop_loss=row.enable_stop_loss,
            trailing_stop_rate=row.trailing_stop_rate,
            enable_trailing_stop=row.enable_trailing_stop,
            trailing_stop_activation_profit_rate=(
                row.trailing_stop_activation_profit_rate
            ),
            current_stop_price=row.current_stop_price,
        )


class GridPositionManager:
    """线程安全的逻辑仓位管理器（账本驱动）"""

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
            self._positions[position.entry_id] = position

    def _remove_position(self, custom_id: str) -> bool:
        """根据custom_id移除逻辑仓位"""
        with self._lock:
            if custom_id in self._positions:
                del self._positions[custom_id]
                return True
            return False

    def load_positions(self, force: bool = False) -> bool:
        """
        从账本加载逻辑仓位（覆盖内存）
        @param force 强制重读; 运行期内存是权威状态, 只有启动时必须加载
        """
        if not force:
            return False

        with self._lock:
            rows = self._journal.load_positions(self._strategy_key)
            logger.info("从账本加载逻辑仓位: %s 笔", len(rows))
            for row in rows:
                position = GridPosition.from_row(row)
                self._positions[position.entry_id] = position
            return True

    def record_positions(
        self,
        closed_positions: list[GridPosition] | None = None,
        refresh_orders: bool = False,
    ) -> None:
        """
        同步仓位快照到账本

        @param closed_positions: 本次平仓的仓位（归档, 移出当前集合）
        @param refresh_orders: 调用方是否已确认状态发生变化
        """
        if closed_positions is None:
            closed_positions = []
        if not refresh_orders and not closed_positions:
            return

        with self._lock:
            for position in closed_positions:
                self._remove_position(position.entry_id)
            rows = [
                position.to_row(self._strategy_key)
                for position in self._positions.values()
            ]
            removed = [position.entry_id for position in closed_positions]
            self._journal.sync_positions(self._strategy_key, rows, removed)


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

    # 平仓比例, 大于 1 会反向加仓
    close_position_ratio: float = Field(default=1.0, gt=0, le=1)

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

        # 启动对账: 先用订单号确认遗留的未完成下单, 再从账本恢复订单
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

    def place_order(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        intent: OrderIntent,
        first_price: float | None = None,
    ) -> OrderResult | None:
        """
        下单入口: 订单号由账本在发送前分发, 记账下单保证交易所与账本最终一致
        @param side: 买卖方向(未做反向处理前)
        @param qty: 数量
        @param price: 价格
        @param intent: 本次下单的意图快照（崩溃后对账重建的依据）
        @param first_price: 追单第一档价格
        @return: 交易所返回的订单结果
        """
        if self.config.position_reverse:
            position_side = (
                PositionSide.SHORT
                if self.config.position_side == PositionSide.LONG
                else PositionSide.LONG
            )
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        else:
            position_side = self.config.position_side

        request = OrderRequest(
            custom_id="",  # 订单号由账本在发送前分发, 保证先登记后下单
            symbol=self.config.symbol,
            side=side,
            type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            position_side=position_side,
        )
        placer = JournaledTradingPort(
            trading=self.trading,
            journal=self.journal,
            strategy_key=self.config.strategy_key,
            intent=intent,
        )

        if self.config.place_order_behavior == PlaceOrderBehavior.NORMAL:
            return placer.place(request)

        chaser = LimitOrderChaser(
            trading=placer,
            market=self.market,
            symbol=self.config.symbol,
            side=side,
            quantity=qty,
            position_side=position_side,
            behavior=self.config.place_order_behavior,
        )
        result = chaser.run(price, first_price=first_price)
        if result is not None:
            return result

        # 追单耗尽仍未挂上: 降级为常规限价单, 避免静默丢单
        logger.warning(
            "追单未成功, 降级为常规限价单: %s, %s, Qty: %s",
            self.config.symbol.ccxt(),
            intent.kind.value,
            qty,
        )
        return placer.place(request)

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
            order_id = build_order_id(self.config.master_side)
            # Initialize stop loss settings
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

            # Calculate initial stop price
            current_stop_price = None
            if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
                if self.config.master_side == OrderSide.BUY:
                    current_stop_price = close_price * (1 - stop_loss_rate)
                else:  # SELL
                    current_stop_price = close_price * (1 + stop_loss_rate)

            position = GridPosition(
                entry_id=order_id,
                side=self.config.master_side,
                price=close_price,
                quantity=self.config.per_order_qty,
                fixed_take_profit_rate=self.config.fixed_take_profit_rate,
                signal_min_take_profit_rate=self.config.exit_signal_take_profit_min_rate,
                status=OrderStatus.OPEN,
                stop_loss_rate=stop_loss_rate,
                enable_stop_loss=self.config.enable_order_stop_loss,
                trailing_stop_rate=trailing_stop_rate,
                enable_trailing_stop=self.config.enable_trailing_stop,
                trailing_stop_activation_profit_rate=trailing_activation_rate,
                current_stop_price=current_stop_price,
            )

            if self.config.per_order_qty == 0:
                position.status = OrderStatus.CLOSED
            else:
                entry_order_result = self.place_order(
                    self.config.master_side,
                    self.config.per_order_qty,
                    close_price,
                    first_price=close_price,
                    intent=OrderIntent(
                        kind=OrderRole.ENTRY,
                        symbol=self.config.symbol,
                        side=self.config.master_side,
                        entry_position=position.to_row(self.config.strategy_key),
                    ),
                )
                if entry_order_result is not None:
                    # 入场订单ID可能因为追单行为而改变,所以使用返回的订单ID
                    position.entry_id = entry_order_result.custom_id
                    if entry_order_result.price is not None:
                        position.price = entry_order_result.price
                    position.status = entry_order_result.status
                    position.filled_quantity = entry_order_result.filled_qty
            self.position_manager.add_position(position)
            return True
        return False

    def check_close_position(self) -> list[GridPosition]:
        if self.latest_kline_obj is None:
            return []

        current_positions = self.position_manager.positions
        exit_signal = (
            self.config.enable_exit_signal
            and self.config.signal
            and self.config.signal.is_exit(self.klines_df)
        )

        remove_positions: list[GridPosition] = []
        exit_positions: list[GridPosition] = []
        exit_qty = 0
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for position in current_positions:
            profit_level = position.profit_level(self.latest_kline_obj.close)

            # 检查止损条件
            stop_loss_triggered = False
            if position.enable_stop_loss and position.current_stop_price is not None:
                if position.side == OrderSide.BUY:
                    stop_loss_triggered = (
                        self.latest_kline_obj.close <= position.current_stop_price
                    )
                else:  # SELL
                    stop_loss_triggered = (
                        self.latest_kline_obj.close >= position.current_stop_price
                    )

            if (
                stop_loss_order_all
                or (profit_level == 2 and self.config.fixed_rate_take_profit)
                or (profit_level == 1 and exit_signal)
                or stop_loss_triggered
            ):
                if position.status is not None and position.status.is_open():
                    entry_order_query_result = self.trading.query(
                        position.entry_id, self.config.symbol
                    )
                    if entry_order_query_result is None:
                        position.status = OrderStatus.EXPIRED
                    else:
                        position.status = entry_order_query_result.status
                        position.filled_quantity = entry_order_query_result.filled_qty

                    if position.status.is_open():
                        # 未成交完的入场单: 先撤掉剩余挂单防止继续建仓,
                        # 撤单后再复查终态(撤单前可能又有成交)
                        refreshed = self._cancel_and_refresh(position.entry_id)
                        if refreshed is not None:
                            position.status = refreshed.status
                            position.filled_quantity = refreshed.filled_qty

                    # 只有零成交才直接丢弃; 已成交的部分是真持仓, 必须纳入平仓
                    if (
                        not position.status.is_closed()
                        and position.effective_filled_quantity <= 0
                    ):
                        remove_positions.append(position)
                        continue

                remaining_qty = position.effective_filled_quantity
                if position.exit_id and position.exit_price:
                    exit_order_query_result = self.trading.query(
                        position.exit_id, self.config.symbol
                    )
                    if exit_order_query_result is not None:
                        if exit_order_query_result.status.is_closed():
                            remove_positions.append(position)
                            continue
                        if exit_order_query_result.status.is_open():
                            # 撤掉未成交完的旧出场单, 撤单后再复查终态
                            refreshed = self._cancel_and_refresh(position.exit_id)
                            if refreshed is not None:
                                exit_order_query_result = refreshed
                        # 出场单已成交的部分不能重复平: 从待平量中扣除
                        remaining_qty = max(
                            0.0,
                            remaining_qty - exit_order_query_result.filled_qty,
                        )

                if remaining_qty <= 0:
                    # 出场单实际已全部成交(仅本地状态未刷新): 平仓已完成, 直接归档
                    remove_positions.append(position)
                    continue

                exit_qty += remaining_qty
                position.exit_price = self.latest_kline_obj.close
                exit_positions.append(position)

        if exit_qty > 0:
            exit_order_side = self.config.master_side.reversal()
            # 精度按 step_size 由交易所适配器向下取整, 这里只做比例换算
            actual_exit_qty = exit_qty * self.config.close_position_ratio
            execute_exit_order_result = self.place_order(
                exit_order_side,
                actual_exit_qty,
                self.latest_kline_obj.close,
                intent=OrderIntent(
                    kind=OrderRole.EXIT,
                    symbol=self.config.symbol,
                    side=exit_order_side,
                    entry_ids=[position.entry_id for position in exit_positions],
                ),
            )
            if execute_exit_order_result is not None:
                for position in exit_positions:
                    position.exit_id = execute_exit_order_result.custom_id
                    if execute_exit_order_result.price is not None:
                        position.exit_price = execute_exit_order_result.price

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return exit_positions + remove_positions

    def _cancel_and_refresh(self, custom_id: str) -> OrderResult | None:
        """
        撤掉未成交完的挂单并复查终态

        撤单请求发出前可能又有成交, 因此复查一次拿最终的成交量与状态;
        复查失败(返回 None)时由调用方沿用已知状态。
        """
        self.trading.cancel(custom_id, self.config.symbol)
        return self.trading.query(custom_id, self.config.symbol)

    def _on_kline_finished(self):
        if not self.is_running or self.latest_kline_obj is None:
            return

        refresh = False

        # 更新跟踪止损
        extremum_price = (
            self.latest_kline_obj.high
            if self.config.master_side == OrderSide.BUY
            else self.latest_kline_obj.low
        )
        current_positions = self.position_manager.positions
        for position in current_positions:
            if not position.enable_trailing_stop or position.current_stop_price is None:
                continue

            # 检查是否达到激活盈利条件
            activation_price = position.price * (
                1 + position.trailing_stop_activation_profit_rate * position.side.to_int()
            )
            if position.side.compare_fun(and_eq=True)(extremum_price, activation_price):
                new_stop_price = extremum_price * (
                    1 - position.trailing_stop_rate * position.side.to_int()
                )
                position.current_stop_price = position.side.reversal().extremum_fun()(
                    position.current_stop_price, new_stop_price
                )
                refresh = True

        if not self.check_open_position():
            closed_positions = self.check_close_position()
        else:
            closed_positions = []
            refresh = True

        self.position_manager.record_positions(closed_positions, refresh)

    def _on_kline(self):
        if self.latest_kline_obj is None:
            return

        positions_to_process = self.position_manager.positions
        refresh_orders = False

        # 检查是否需要触发实时止盈订单
        closed_positions: list[GridPosition] = []
        if (
            self.config.fixed_rate_take_profit
            and self.config.take_profit_use_limit_order
        ):
            for position in positions_to_process:
                if position.quantity == 0:
                    continue

                if position.exit_id and position.exit_price:
                    # 检查退出是否成交
                    if (
                        self.latest_kline_obj.low
                        <= position.exit_price
                        <= self.latest_kline_obj.high
                    ):
                        exit_order_query_result = self.trading.query(
                            position.exit_id, self.config.symbol
                        )
                        if (
                            exit_order_query_result is not None
                            and exit_order_query_result.status.is_closed()
                        ):
                            closed_positions.append(position)
                else:
                    # 检查进入订单是否成交
                    if position.status is not None and position.status.is_open():
                        entry_order_query_result = self.trading.query(
                            position.entry_id, self.config.symbol
                        )
                        if entry_order_query_result is not None:
                            position.status = entry_order_query_result.status
                            position.filled_quantity = entry_order_query_result.filled_qty

                    # 如果进入订单成交，触发实时止盈订单
                    if position.status is not None and position.status.is_closed():
                        exit_order_side = self.config.master_side.reversal()
                        exit_price = position.price * (
                            1
                            + self.config.master_side.to_int()
                            * self.config.fixed_take_profit_rate
                        )
                        exit_qty = (
                            position.effective_filled_quantity
                            * self.config.close_position_ratio
                        )

                        exit_order_result = self.place_order(
                            exit_order_side,
                            exit_qty,
                            exit_price,
                            first_price=exit_price,
                            intent=OrderIntent(
                                kind=OrderRole.EXIT,
                                symbol=self.config.symbol,
                                side=exit_order_side,
                                entry_ids=[position.entry_id],
                            ),
                        )
                        if exit_order_result is not None:
                            position.exit_id = exit_order_result.custom_id
                            position.exit_price = exit_price
                            refresh_orders = True

            self.position_manager.record_positions(
                closed_positions, refresh_orders=refresh_orders
            )
