"""SignalGridStrategy v3 —— 与 v2 行为一致的重构版本。

重构要点(行为不变):
1. 平仓原因建模为 ``CloseReason`` 枚举; 平仓检查结果拆分为 ``CloseResult``
   (真正下出平仓单的订单 / 仅被移除的未成交订单), 不再把两类语义拼进同一个列表。
2. ``check_close_order`` 内部拆为: 判断原因 ``_close_reason`` +
   同步入场单状态 ``_sync_entry_status`` + 下单执行 ``_submit_batch_exit_order``。
3. ``profit_level`` 的魔法数字 -1/0/1/2 改为 ``ProfitLevel`` 枚举。
4. 移除 ``OrderRecorder.record`` 中恒为 False 的死代码。
5. 复用 ``OrderSide`` 的 ``compare_fun`` / ``extremum_fun`` / ``reversal`` / ``to_int``,
   消除手写的 BUY/SELL 分支。
"""

import os
import secrets
import threading
from collections.abc import Callable
from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import (
    Kline,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
)
from smart_trader.strategy import Signal, SingleTimeframeStrategy

logger = log.getLogger(__name__)


def build_order_id(side: OrderSide):
    return f"{side.value}{secrets.token_hex(nbytes=5)}"


class ProfitLevel(IntEnum):
    """订单相对当前价格的盈亏级别"""

    LOSS = -1  # 亏损中
    BREAK_EVEN = 0  # 盈利无法覆盖手续费
    PROFIT = 1  # 盈利中
    TAKE_PROFIT = 2  # 达到止盈标准


class CloseReason(Enum):
    """订单被平仓/移除的原因"""

    CLOSE_ALL = "close_all"  # 达到最大订单数全平 或 手动平仓
    FIXED_TAKE_PROFIT = "fixed_take_profit"  # 固定比例止盈
    EXIT_SIGNAL = "exit_signal"  # 退出信号止盈
    STOP_LOSS = "stop_loss"  # 单笔止损触发


class Order(BaseModel):
    entry_id: str
    side: OrderSide
    price: float
    quantity: float
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    exit_price: float | None = None
    status: str | None = None
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
        if isinstance(other, Order):
            return self.entry_id == other.entry_id
        return False

    @property
    def has_exit_order(self) -> bool:
        """是否已经挂出平仓单"""
        return bool(self.exit_id and self.exit_price)

    @property
    def is_open(self) -> bool:
        """入场单是否仍处于未成交状态"""
        return OrderStatus.is_open(self.status)

    @property
    def is_closed(self) -> bool:
        """入场单是否已成交"""
        return OrderStatus.is_closed(self.status)

    def profit_level(self, current_price: float) -> ProfitLevel:
        """计算订单的盈利级别"""
        compare_fun = self.side.compare_fun()

        if compare_fun(current_price, self._profit(self.fixed_take_profit_rate)):
            return ProfitLevel.TAKE_PROFIT
        elif compare_fun(current_price, self._profit(self.signal_min_take_profit_rate)):
            return ProfitLevel.PROFIT
        elif compare_fun(current_price, self.price):
            return ProfitLevel.BREAK_EVEN

        return ProfitLevel.LOSS

    def profit_and_loss_ratio(self, current_price: float) -> float:
        """盈亏率: 盈利为正, 亏损为负"""
        loss_rate = float(f"{abs(current_price - self.price) / self.price:.6f}")
        if self.profit_level(current_price) == ProfitLevel.LOSS:
            return -loss_rate
        else:
            return loss_rate

    def _profit(self, rate: float) -> float:
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)


class CloseResult(BaseModel):
    """一次平仓检查的结果

    @param exit_orders 真正触发并下出平仓单的订单
    @param removed_orders 未成交入场单被同步/撤销后从持仓中移除的订单
    """

    exit_orders: list[Order] = []
    removed_orders: list[Order] = []

    @property
    def all_orders(self) -> list[Order]:
        """本次需要从 order_manager 中移除的全部订单"""
        return self.exit_orders + self.removed_orders


class OrderRecorder(BaseModel):
    """
    订单记录器
    @param order_file_path 订单文件路径
    @param orders 当前订单
    @param history_orders 历史订单
    @param is_reload 程序中通常不会直接设置该值,而是用户在需要重新加载时在本地备份文件中设置True,实现热更新的效果
    """

    order_file_path: str
    orders: list[Order] = []
    history_orders: list[Order] = []
    is_reload: bool = False

    def record(
        self,
        latest_orders: list[Order],
        closed_orders: list[Order],
        refresh_orders: bool = False,
    ):
        self.orders = latest_orders

        if closed_orders:
            self.history_orders += closed_orders
            refresh_orders = True

        if refresh_orders and self.order_file_path:
            with open(self.order_file_path, "w") as f:
                f.write(self.model_dump_json())

    def check_reload(self, force: bool = False) -> list[Order] | None:
        """
        从本地文件中读取订单，并检查是否需要重新加载
        @param force 强制重新加载
        """
        if self.order_file_path and os.path.exists(self.order_file_path):
            with open(self.order_file_path, "r") as f:
                _recorder = OrderRecorder.model_validate_json(f.read())
                if _recorder.is_reload or force:
                    logger.info(
                        "Reload orders from %s, force=%s",
                        self.order_file_path,
                        force,
                    )
                    return _recorder.orders
        return None


class OrderManager:
    """线程安全的订单管理器"""

    def __init__(self, order_file_path: str):
        self._orders: dict[str, Order] = {}
        self._lock = threading.RLock()
        self._order_recorder = OrderRecorder(order_file_path=order_file_path)

    @property
    def orders(self) -> list[Order]:
        """获取订单列表的线程安全副本"""
        with self._lock:
            return list(self._orders.values())

    def add_order(self, order: Order) -> None:
        """添加订单"""
        with self._lock:
            self._orders[order.entry_id] = order

    def _remove_order(self, custom_id: str) -> bool:
        """根据custom_id移除订单"""
        with self._lock:
            if custom_id in self._orders:
                del self._orders[custom_id]
                return True
            return False

    def load_orders(self, force: bool = False) -> bool:
        """从文件加载订单"""
        with self._lock:
            orders = self._order_recorder.check_reload(force=force)
            if orders is None:
                return False
            for order in orders:
                self.add_order(order)
            return True

    def record_orders(
        self, closed_orders: list[Order] | None = None, refresh_orders: bool = False
    ) -> None:
        """
        记录订单到文件, 如果closed_orders为空, 则只记录当前订单
        @param closed_orders 已经关闭订单
        @param refresh_orders 刷新到文件
        """
        if closed_orders is None:
            closed_orders = []

        with self._lock:
            for order in closed_orders:
                self._remove_order(order.entry_id)
            self._order_recorder.record(self.orders, closed_orders, refresh_orders)


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

    close_position_ratio: float = 1.0

    place_order_behavior: PlaceOrderBehavior = (
        PlaceOrderBehavior.CHASER_OPEN
    )  # 下单行为

    order_file_path: str = "data/grids_strategy_v2.json"

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
    def __init__(self, config: SignalGridStrategyConfig, ex_client: ExSwapClient):
        super().__init__(config.timeframe)
        self.config = config
        self.ex_client = ex_client

        self.order_manager = OrderManager(order_file_path=self.config.order_file_path)
        self.order_manager.load_orders(True)

        self.on_stop_loss_order_all: Callable[[], None] = lambda: None
        self.close_position: bool = False
        self.is_running: bool = True

    def exchange_client(self) -> ExSwapClient:
        return self.ex_client

    def place_order(
        self,
        order_id: str,
        side: OrderSide,
        qty: float,
        price: float,
        first_price: float | None = None,
    ):
        if self.config.position_reverse:
            position_side = (
                PositionSide.SHORT
                if self.config.position_side == PositionSide.LONG
                else PositionSide.LONG
            )
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        else:
            position_side = self.config.position_side

        return self.ex_client.place_order_v2(
            custom_id=order_id,
            symbol=self.config.symbol,
            order_side=side,
            quantity=qty,
            price=price,  # place_order_behavior == NORMAL 价格才会生效
            position_side=position_side,
            place_order_behavior=self.config.place_order_behavior,
            first_price=first_price,
        )

    # ------------------------------------------------------------------ 开仓

    def _reached_order_limit_for_stop_all(self) -> bool:
        """订单数达到 max_order - 1 时, 触发全部止损"""
        return (
            self.config.enable_max_order_stop_loss
            and self.config.max_order - len(self.order_manager.orders) <= 1
        )

    def _most_recent_price_order(self) -> Order | None:
        """离当前价格最近的一笔订单(买单取最低价, 卖单取最高价)"""
        orders = self.order_manager.orders
        if not orders:
            return None
        return self.config.master_side.extremum_fun()(
            orders, key=lambda order: order.price
        )

    def _should_place_next_entry(self, close_price: float) -> bool:
        """判断当前价格是否满足补仓间距, 且未触及"全部止损"上限"""
        recent_price_order = self._most_recent_price_order()
        spacing_reached = recent_price_order is None or (
            recent_price_order.profit_and_loss_ratio(close_price)
            <= -self.config.grid_spacing_rate
        )
        if not spacing_reached:
            return False
        return not self._reached_order_limit_for_stop_all()

    def _build_entry_order(self, close_price: float) -> Order:
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

        initial_stop_price: float | None = None
        if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
            # 买单止损价在下, 卖单止损价在上
            initial_stop_price = close_price * (
                1 - stop_loss_rate * self.config.master_side.to_int()
            )

        return Order(
            entry_id=build_order_id(self.config.master_side),
            side=self.config.master_side,
            price=close_price,
            quantity=self.config.per_order_qty,
            fixed_take_profit_rate=self.config.fixed_take_profit_rate,
            signal_min_take_profit_rate=self.config.exit_signal_take_profit_min_rate,
            status=OrderStatus.OPEN.value,
            stop_loss_rate=stop_loss_rate,
            enable_stop_loss=self.config.enable_order_stop_loss,
            trailing_stop_rate=trailing_stop_rate,
            enable_trailing_stop=self.config.enable_trailing_stop,
            trailing_stop_activation_profit_rate=trailing_activation_rate,
            current_stop_price=initial_stop_price,
        )

    def _submit_entry_order(self, order: Order) -> None:
        """提交入场单; 追单会使订单ID变化, 因此以下单返回值为准"""
        if order.quantity == 0:
            order.status = OrderStatus.CLOSED.value
            return

        entry_order_result = self.place_order(
            order.entry_id,
            order.side,
            order.quantity,
            order.price,
            first_price=order.price,
        )
        if entry_order_result and entry_order_result.get("clientOrderId"):
            order.entry_id = entry_order_result["clientOrderId"]
            order.price = entry_order_result["price"]
            order.status = entry_order_result["status"]

    def check_open_order(self) -> bool:
        """检查是否需要开新仓; 开仓时会下单并写入 order_manager, 返回是否开仓"""
        # 检查订单是否到达上限
        if len(self.order_manager.orders) >= self.config.max_order:
            return False

        # 检查是否有入场信号
        if self.config.signal and not self.config.signal.is_entry(self.klines_df):
            return False

        latest = self.latest_kline_obj
        if latest is None:
            return False

        # 检查当前价格是否在可交易的价格区间
        close_price = latest.close
        if not (self.config.lowest_price <= close_price <= self.config.highest_price):
            return False

        if not self._should_place_next_entry(close_price):
            return False

        order = self._build_entry_order(close_price)
        self._submit_entry_order(order)
        self.order_manager.add_order(order)
        return True

    # ------------------------------------------------------------------ 平仓

    def _stop_loss_triggered(self, order: Order, close_price: float) -> bool:
        """单笔止损是否触发: 买单跌破止损价 / 卖单涨破止损价"""
        if not (order.enable_stop_loss and order.current_stop_price is not None):
            return False
        # reversal 后 compare_fun(and_eq=True): 买单为 <=, 卖单为 >=
        reached_stop = order.side.reversal().compare_fun(and_eq=True)
        return reached_stop(close_price, order.current_stop_price)

    def _close_reason(
        self,
        order: Order,
        close_price: float,
        exit_signal: bool,
        close_all: bool,
    ) -> CloseReason | None:
        """返回订单的平仓原因, 无则返回 None"""
        if close_all:
            return CloseReason.CLOSE_ALL

        profit_level = order.profit_level(close_price)
        if profit_level == ProfitLevel.TAKE_PROFIT and self.config.fixed_rate_take_profit:
            return CloseReason.FIXED_TAKE_PROFIT
        if profit_level == ProfitLevel.PROFIT and exit_signal:
            return CloseReason.EXIT_SIGNAL
        if self._stop_loss_triggered(order, close_price):
            return CloseReason.STOP_LOSS
        return None

    def _sync_entry_status(self, order: Order, *, expire_when_missing: bool) -> None:
        """同步入场单在交易所的状态

        两个调用点对"查询无结果"的语义不同: 平仓检查视为 EXPIRED,
        实时限价止盈则保持原状态, 因此用参数区分。
        """
        if not order.is_open:
            return

        result = self.ex_client.query_order(order.entry_id, self.config.symbol)
        if result is None:
            if expire_when_missing:
                order.status = OrderStatus.EXPIRED.value
            return
        order.status = result["status"]

    def _remove_unfilled_entry(self, order: Order) -> bool:
        """入场单尚未成交时同步状态; 返回 True 表示该订单应从持仓中移除"""
        if not order.is_open:
            return False

        self._sync_entry_status(order, expire_when_missing=True)
        if order.is_closed:
            return False
        if order.is_open:
            self.ex_client.cancel(order.entry_id, self.config.symbol)
        return True

    def _remove_closed_exit_order(self, order: Order) -> bool:
        """已挂平仓单时同步其状态; 返回 True 表示该订单应从持仓中移除"""
        if not order.has_exit_order:
            return False

        assert order.exit_id is not None
        exit_order_result = self.ex_client.query_order(order.exit_id, self.config.symbol)
        if not exit_order_result:
            return False

        exit_status = exit_order_result["status"]
        if OrderStatus.is_closed(exit_status):
            return True
        if OrderStatus.is_open(exit_status):
            self.ex_client.cancel(order.exit_id, self.config.symbol)
        return False

    def _submit_batch_exit_order(
        self, exit_orders: list[Order], exit_qty: float, close_price: float
    ) -> None:
        """把本轮触发的订单合并成一笔反向平仓单"""
        exit_order_side = self.config.master_side.reversal()
        exit_order_id = build_order_id(exit_order_side)
        actual_exit_qty = exit_qty * self.config.close_position_ratio
        execute_exit_order_result = self.place_order(
            exit_order_id,
            exit_order_side,
            actual_exit_qty,
            close_price,
        )
        if execute_exit_order_result:
            for order in exit_orders:
                order.exit_id = execute_exit_order_result["clientOrderId"]
                order.exit_price = execute_exit_order_result["price"]

    def check_close_order(self) -> CloseResult:
        """止盈/止损/退出信号检查; 会查询/撤销/下平仓单, 返回本次变动的订单"""
        latest = self.latest_kline_obj
        if latest is None:
            return CloseResult()

        close_price = latest.close
        exit_signal = (
            self.config.enable_exit_signal
            and self.config.signal is not None
            and self.config.signal.is_exit(self.klines_df)
        )
        close_all = self._reached_order_limit_for_stop_all() or self.close_position

        result = CloseResult()
        exit_qty = 0.0
        for order in self.order_manager.orders:
            if self._close_reason(order, close_price, exit_signal, close_all) is None:
                continue

            if self._remove_unfilled_entry(order):
                result.removed_orders.append(order)
                continue

            if self._remove_closed_exit_order(order):
                result.removed_orders.append(order)
                continue

            exit_qty += order.quantity
            order.exit_price = close_price
            result.exit_orders.append(order)

        if exit_qty > 0:
            self._submit_batch_exit_order(result.exit_orders, exit_qty, close_price)

        if self.close_position:
            self.close_position = False

        if close_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return result

    # -------------------------------------------------------------- K线回调

    def _update_trailing_stops(self, latest: Kline) -> bool:
        """更新跟踪止损价; 返回是否有止损价发生变动"""
        extremum_price = (
            latest.high if self.config.master_side == OrderSide.BUY else latest.low
        )
        changed = False
        for order in self.order_manager.orders:
            if not order.enable_trailing_stop or order.current_stop_price is None:
                continue

            # 检查是否达到激活盈利条件
            activation_price = order.price * (
                1 + order.trailing_stop_activation_profit_rate * order.side.to_int()
            )
            if order.side.compare_fun(and_eq=True)(extremum_price, activation_price):
                new_stop_price = extremum_price * (
                    1 - order.trailing_stop_rate * order.side.to_int()
                )
                order.current_stop_price = order.side.reversal().extremum_fun()(
                    order.current_stop_price, new_stop_price
                )
                changed = True
        return changed

    def _on_kline_finished(self):
        latest = self.latest_kline_obj
        if not self.is_running or latest is None:
            return

        # 检查是否需要重新加载订单
        refresh = self.order_manager.load_orders()
        refresh = self._update_trailing_stops(latest) or refresh

        # 止损/平仓优先: 本根K线一旦产生平仓(含撤销未成交入场单), 就不再开新仓
        close_result = self.check_close_order()
        closed_orders = close_result.all_orders
        if closed_orders or self.check_open_order():
            refresh = True

        self.order_manager.record_orders(closed_orders, refresh)

    def _sync_limit_take_profit_orders(self, latest: Kline) -> None:
        """实时限价止盈: 入场成交后挂出止盈单, 并回收已成交的止盈单"""
        refresh_orders = False
        closed_orders: list[Order] = []
        for order in self.order_manager.orders:
            if order.quantity == 0:
                continue

            if order.has_exit_order:
                assert order.exit_price is not None
                # 检查退出是否成交
                if latest.low <= order.exit_price <= latest.high:
                    assert order.exit_id is not None
                    exit_order_query_result = self.ex_client.query_order(
                        order.exit_id, self.config.symbol
                    )
                    if exit_order_query_result and OrderStatus.is_closed(
                        exit_order_query_result["status"]
                    ):
                        closed_orders.append(order)
            else:
                # 检查进入订单是否成交
                self._sync_entry_status(order, expire_when_missing=False)

                # 如果进入订单成交，触发实时止盈订单
                if order.is_closed:
                    exit_order_side = self.config.master_side.reversal()
                    exit_order_id = build_order_id(exit_order_side)
                    exit_price = order.price * (
                        1
                        + self.config.master_side.to_int()
                        * self.config.fixed_take_profit_rate
                    )
                    exit_qty = order.quantity * self.config.close_position_ratio

                    exit_order_result = self.place_order(
                        exit_order_id,
                        exit_order_side,
                        exit_qty,
                        exit_price,
                        first_price=exit_price,
                    )
                    if exit_order_result and exit_order_result.get("clientOrderId"):
                        order.exit_id = exit_order_result["clientOrderId"]
                        order.exit_price = exit_price
                        refresh_orders = True

        self.order_manager.record_orders(closed_orders, refresh_orders=refresh_orders)

    def _on_kline(self):
        latest = self.latest_kline_obj
        if latest is None:
            return

        # 只有开启限价止盈时才需要实时处理
        if not (
            self.config.fixed_rate_take_profit
            and self.config.take_profit_use_limit_order
        ):
            return

        self._sync_limit_take_profit_orders(latest)