import os
import secrets
import threading
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, model_validator

from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import (
    OrderPhase,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
)
from smart_trader.strategy import Signal, SingleTimeframeStrategy
from smart_trader.strategy.position_reconciler import PositionReconciler

logger = log.getLogger(__name__)


def build_order_id(side: OrderSide):
    return f"{side.value}{secrets.token_hex(nbytes=5)}"


class Order(BaseModel):
    entry_id: str
    side: OrderSide
    price: float
    quantity: float
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    exit_price: float | None = None
    status: str | None = None
    phase: OrderPhase
    exit_id: str | None = None

    # Stop loss fields
    stop_loss_rate: float = 0.0
    enable_stop_loss: bool = False
    trailing_stop_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_activation_profit_rate: float = 0.0
    current_stop_price: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _infer_phase_for_legacy(cls, data: Any) -> Any:
        """旧版本账本没有 phase 字段, 按已有字段推断生命周期"""
        if not isinstance(data, dict):
            return data
        values = cast(dict[str, Any], data)
        if "phase" in values:
            return values
        if values.get("exit_id"):
            values["phase"] = OrderPhase.EXIT_PENDING
        elif OrderStatus.is_closed(values.get("status")):
            values["phase"] = OrderPhase.HOLDING
        else:
            values["phase"] = OrderPhase.ENTRY_PENDING
        return values

    def __hash__(self):
        return hash(self.entry_id)

    def __eq__(self, other: object):
        if isinstance(other, Order):
            return self.entry_id == other.entry_id
        return False

    def profit_level(self, current_price: float) -> int:
        """
        计算订单的盈利级别
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


class OrderRecorder(BaseModel):
    """
    订单记录器
    @param order_file_path 订单文件路径
    @param orders 当前订单
    @param history_orders 历史订单
    @param is_reload 程序中通常不会直接设置该值,而是用户在需要重新加载时在本地备份文件中设置True,实现热更新的效果
    @param reload_msg 重新加载消息
    @param total_profit 总利润
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
                f.write(self.model_dump_json() + '\n\n')

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

    @property
    def history_orders(self) -> list[Order]:
        """历史订单 (已完结, 用于审计与测试观测)"""
        with self._lock:
            return list(self._order_recorder.history_orders)

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
        @param closed_orders 已经关闭订单 (去重: 只有仍在账本中的订单才会移入历史)
        @param refresh_orders 刷新到文件
        """
        if closed_orders is None:
            closed_orders = []

        with self._lock:
            confirmed: list[Order] = []
            for order in closed_orders:
                if self._remove_order(order.entry_id):
                    confirmed.append(order)
            self._order_recorder.record(self.orders, confirmed, refresh_orders)


class CloseCheckResult(BaseModel):
    """一次平仓检查的结果

    @param removed_orders 被撤销/完结, 需从账本移除的订单 (未成交入场单)
    @param exited_orders 已提交平仓单、转为 EXIT_PENDING 保留在账本的订单
    """

    removed_orders: list[Order] = []
    exited_orders: list[Order] = []


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

    # 仓位对账最小间隔秒数, <=0 关闭对账
    reconcile_interval_sec: float = 1800.0

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

        self.reconciler: PositionReconciler | None = (
            PositionReconciler(
                ex_client=ex_client,
                symbol=config.symbol,
                position_side=self._exchange_position_side(),
                interval_sec=config.reconcile_interval_sec,
            )
            if config.reconcile_interval_sec > 0
            else None
        )

    def exchange_client(self) -> ExSwapClient:
        return self.ex_client

    def _exchange_position_side(self) -> PositionSide:
        """本策略在交易所的实际仓位方向 (反转策略取反)"""
        if self.config.position_reverse:
            return (
                PositionSide.SHORT
                if self.config.position_side == PositionSide.LONG
                else PositionSide.LONG
            )
        return self.config.position_side

    def place_order(
        self,
        order_id: str,
        side: OrderSide,
        qty: float,
        price: float,
        first_price: float | None = None,
    ):
        if self.config.position_reverse:
            position_side = self._exchange_position_side()
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

    def _check_max_order_stop_loss(self) -> bool:
        return (
            self.config.enable_max_order_stop_loss
            and self.config.max_order - len(self.order_manager.orders) <= 1
        )

    # ------------------------------------------------------------ 状态机同步

    def _query_order(self, custom_id: str) -> dict[str, Any] | None:
        """查询交易所订单; 订单不存在返回 None, 其他异常向上抛"""
        return self.ex_client.query_order(custom_id, self.config.symbol)

    def _cancel_order_safe(self, custom_id: str) -> None:
        """撤销订单; 失败 (已成交/已撤销/网络) 只记日志, 不中断当根K线"""
        try:
            self.ex_client.cancel(custom_id, self.config.symbol)
        except Exception:
            logger.exception("撤销订单失败 (订单可能已成交或已撤销): %s", custom_id)

    def _retire(self, order: Order) -> None:
        """订单完结: 标记 CLOSED (由调用方收集并经 record_orders 移入历史)"""
        order.phase = OrderPhase.CLOSED

    def _revert_to_holding(self, order: Order) -> None:
        """平仓单未成交已撤销: 订单回到持仓态, 等待下次触发重新平仓"""
        logger.info("平仓单已撤销, 订单回到持仓态: entry=%s", order.entry_id)
        order.phase = OrderPhase.HOLDING
        order.exit_id = None
        order.exit_price = None

    def sync_order_phases(self) -> list[Order]:
        """推进订单状态机: 同步入场/平仓单在交易所的真实状态

        查询失败时保持原状态, 等待下根K线重试, 绝不因查询失败改变账本。

        @return 已确认完结、应移入历史的订单
        """
        retired: list[Order] = []
        for order in self.order_manager.orders:
            if order.phase == OrderPhase.ENTRY_PENDING:
                self._sync_entry_phase(order, retired)
            elif order.phase == OrderPhase.EXIT_PENDING:
                self._sync_exit_phase(order, retired)
        return retired

    def _sync_entry_phase(self, order: Order, retired: list[Order]) -> None:
        """同步 ENTRY_PENDING 订单: 成交转持仓, 失效完结, 部分成交修正数量"""
        try:
            result = self._query_order(order.entry_id)
            if result is None:
                # 订单不存在: 从未成交即失效
                order.status = OrderStatus.EXPIRED.value
                self._retire(order)
                retired.append(order)
                return

            order.status = result["status"]
            if OrderStatus.is_open(order.status):
                return  # 挂单中, 继续等待

            filled = float(result.get("filled") or 0.0)
            if OrderStatus.is_closed(order.status) or filled > 0:
                # 以交易所订单价格修正账本价格 (结果未知路径的入场价只是估计值)
                fill_price = result.get("average") or result.get("price")
                if fill_price:
                    order.price = float(fill_price)
                if not OrderStatus.is_closed(order.status):
                    order.quantity = filled  # 部分成交: 修正为实际成交量
                order.phase = OrderPhase.HOLDING
            else:
                # 已撤销/拒绝/失效且无成交
                self._retire(order)
                retired.append(order)
        except Exception:
            logger.exception(
                "同步入场单状态失败, 保持现状等待下根K线重试: %s", order.entry_id
            )

    def _sync_exit_phase(self, order: Order, retired: list[Order]) -> None:
        """同步 EXIT_PENDING 订单: 平仓成交完结, 撤销则按成交量扣减后回持仓"""
        assert order.exit_id is not None
        try:
            result = self._query_order(order.exit_id)
            if result is None:
                # 平仓单不存在: 视为已撤销, 回到持仓态等待重新触发
                self._revert_to_holding(order)
                return

            status = result["status"]
            if OrderStatus.is_closed(status):
                # 以交易所订单价格修正平仓价 (结果未知路径的平仓价只是估计值)
                fill_price = result.get("average") or result.get("price")
                if fill_price:
                    order.exit_price = float(fill_price)
                self._retire(order)
                retired.append(order)
                return
            if OrderStatus.is_open(status):
                return  # 平仓单挂单中, 继续等待

            # 已撤销/拒绝/失效: 按实际成交量扣减后回到持仓态
            filled = float(result.get("filled") or 0.0)
            remaining = order.quantity - filled
            if remaining <= 0:
                self._retire(order)
                retired.append(order)
            else:
                order.quantity = remaining
                self._revert_to_holding(order)
        except Exception:
            logger.exception(
                "同步平仓单状态失败, 保持现状等待下根K线重试: %s", order.exit_id
            )

    # ------------------------------------------------------------------ 开仓

    def _build_entry_order(self, close_price: float) -> Order:
        """构建入场订单 (ENTRY_PENDING, 含止损/跟踪止损初始参数)"""
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

        return Order(
            entry_id=build_order_id(self.config.master_side),
            side=self.config.master_side,
            price=close_price,
            quantity=self.config.per_order_qty,
            fixed_take_profit_rate=self.config.fixed_take_profit_rate,
            signal_min_take_profit_rate=self.config.exit_signal_take_profit_min_rate,
            status=OrderStatus.OPEN.value,
            phase=OrderPhase.ENTRY_PENDING,
            stop_loss_rate=stop_loss_rate,
            enable_stop_loss=self.config.enable_order_stop_loss,
            trailing_stop_rate=trailing_stop_rate,
            enable_trailing_stop=self.config.enable_trailing_stop,
            trailing_stop_activation_profit_rate=trailing_activation_rate,
            current_stop_price=current_stop_price,
        )

    def check_open_order(self) -> bool:
        # 检查订单是否到达上限
        if len(self.order_manager.orders) >= self.config.max_order:
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

        # 平仓在途的订单不作为网格间距锚点
        anchor_orders = [
            order
            for order in self.order_manager.orders
            if order.phase != OrderPhase.EXIT_PENDING
        ]
        if anchor_orders:
            recent_price_order = self.config.master_side.extremum_fun()(
                anchor_orders, key=lambda order: order.price
            )
        else:
            recent_price_order = None

        if (not recent_price_order) or (
            recent_price_order
            and recent_price_order.profit_and_loss_ratio(close_price)
            <= -self.config.grid_spacing_rate
        ):
            if self._check_max_order_stop_loss():
                return False
            order = self._build_entry_order(close_price)
            if self.config.per_order_qty == 0:
                # 数量为0的订单是永久网格锚点, 不实际下单
                order.status = OrderStatus.CLOSED.value
                order.phase = OrderPhase.HOLDING
            else:
                try:
                    entry_order_result = self.place_order(
                        order.entry_id,
                        self.config.master_side,
                        self.config.per_order_qty,
                        close_price,
                        first_price=close_price,
                    )
                except Exception:
                    # 提交结果未知(如超时): 订单可能已到达交易所。
                    # 以策略生成的 entry_id 入账, 状态机下根K线核实, 查无此单则自然完结。
                    logger.exception(
                        "提交入场单结果未知, 入账待核实: %s", order.entry_id
                    )
                    self.order_manager.add_order(order)
                    return True
                if entry_order_result and entry_order_result.get("clientOrderId"):
                    # 入场订单ID可能因为追单行为而改变,所以使用返回的订单ID
                    order.entry_id = entry_order_result["clientOrderId"]
                    order.price = entry_order_result["price"]
                    order.status = entry_order_result["status"]
            self.order_manager.add_order(order)
            return True
        return False

    def _handle_triggered_pending_entry(self, order: Order) -> bool | None:
        """平仓条件触发时处理未成交入场单

        @return True: 未成交, 已撤销并完结; False: 刚成交, 已转持仓; None: 查询失败, 保持现状
        """
        try:
            result = self._query_order(order.entry_id)
            if result is None:
                order.status = OrderStatus.EXPIRED.value
            else:
                order.status = result["status"]

            if not OrderStatus.is_closed(order.status):
                if OrderStatus.is_open(order.status):
                    self._cancel_order_safe(order.entry_id)
                self._retire(order)
                return True
            order.phase = OrderPhase.HOLDING
            return False
        except Exception:
            logger.exception(
                "查询入场单状态失败, 保持现状等待下根K线: %s", order.entry_id
            )
            return None

    def _submit_batch_exit_order(
        self, exit_orders: list[Order], exit_qty: float, close_price: float
    ) -> None:
        """把本轮触发的持仓单合并为一笔反向平仓单

        订单先标记 EXIT_PENDING 并记录策略生成的平仓单ID (custom_id 全链路复用):
        - 提交成功: 从返回结果补全平仓价
        - 提交结果未知(超时等): 保持 EXIT_PENDING, 由状态机逐K线核实,
          查无此单则回到持仓态重新触发, 已存在则直接接管 —— 不会重复平仓
        """
        exit_order_side = self.config.master_side.reversal()
        exit_order_id = build_order_id(exit_order_side)
        actual_exit_qty = exit_qty * self.config.close_position_ratio
        for order in exit_orders:
            order.exit_id = exit_order_id
            order.phase = OrderPhase.EXIT_PENDING

        try:
            execute_exit_order_result = self.place_order(
                exit_order_id,
                exit_order_side,
                actual_exit_qty,
                close_price,
            )
        except Exception:
            logger.exception(
                "平仓单提交结果未知, 待状态机核实: %s", exit_order_id
            )
            return

        if execute_exit_order_result and execute_exit_order_result.get("price"):
            exit_price = execute_exit_order_result["price"]
            for order in exit_orders:
                order.exit_price = exit_price

    def check_close_order(self) -> CloseCheckResult:
        """止盈/止损/退出信号检查

        触发平仓的持仓单转为 EXIT_PENDING 保留在账本, 由状态机确认成交后移入历史;
        触发平仓条件时仍未成交的入场单被撤销并完结。

        @return 平仓检查结果 (removed_orders 需从账本移除; exited_orders 已转 EXIT_PENDING)
        """
        if self.latest_kline_obj is None:
            return CloseCheckResult()

        current_orders = self.order_manager.orders
        exit_signal = (
            self.config.enable_exit_signal
            and self.config.signal
            and self.config.signal.is_exit(self.klines_df)
        )

        result = CloseCheckResult()
        exit_orders: list[Order] = []
        exit_qty = 0.0
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for order in current_orders:
            if order.phase not in (OrderPhase.ENTRY_PENDING, OrderPhase.HOLDING):
                continue

            profit_level = order.profit_level(self.latest_kline_obj.close)

            # 检查止损条件
            stop_loss_triggered = False
            if order.enable_stop_loss and order.current_stop_price is not None:
                if order.side == OrderSide.BUY:
                    stop_loss_triggered = (
                        self.latest_kline_obj.close <= order.current_stop_price
                    )
                else:  # SELL
                    stop_loss_triggered = (
                        self.latest_kline_obj.close >= order.current_stop_price
                    )

            if (
                stop_loss_order_all
                or (profit_level == 2 and self.config.fixed_rate_take_profit)
                or (profit_level == 1 and exit_signal)
                or stop_loss_triggered
            ):
                if order.phase == OrderPhase.ENTRY_PENDING:
                    handled = self._handle_triggered_pending_entry(order)
                    if handled is None:
                        continue  # 查询失败, 保持现状等待下根K线
                    if handled:
                        result.removed_orders.append(order)
                        continue
                    # 入场单刚成交, 转为持仓继续走平仓流程

                exit_qty += order.quantity
                order.exit_price = self.latest_kline_obj.close
                exit_orders.append(order)

        if exit_qty > 0:
            self._submit_batch_exit_order(
                exit_orders, exit_qty, self.latest_kline_obj.close
            )
            result.exited_orders = exit_orders

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return result

    def _update_trailing_stops(self) -> bool:
        """更新持仓单的跟踪止损价

        @return 是否有止损价发生变动
        """
        latest = self.latest_kline_obj
        if latest is None:
            return False

        extremum_price = (
            latest.high if self.config.master_side == OrderSide.BUY else latest.low
        )
        changed = False
        for order in self.order_manager.orders:
            if order.phase != OrderPhase.HOLDING:
                continue
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

    def _expected_position_qty(self) -> float:
        """账本预期的交易所持仓数量: 持仓中 + 平仓在途 (平仓成交前仓位仍然存在)"""
        return sum(
            order.quantity
            for order in self.order_manager.orders
            if order.phase in (OrderPhase.HOLDING, OrderPhase.EXIT_PENDING)
        )

    def _on_kline_finished(self):
        latest = self.latest_kline_obj
        if latest is None:
            return

        # 热更新: 用户在备份文件中设置 is_reload=True 后生效
        refresh = self.order_manager.load_orders()

        # 状态机同步: 推进入场/平仓单真实状态 (暂停时也要确认在途平仓单)
        retired = self.sync_order_phases()
        closed_orders = retired
        if retired:
            refresh = True

        if self.is_running:
            refresh = self._update_trailing_stops() or refresh

            # 止损/平仓优先: 本根K线一旦产生平仓(含撤销未成交入场单), 就不再开新仓
            close_result = self.check_close_order()
            closed_orders += close_result.removed_orders
            if close_result.removed_orders or close_result.exited_orders or self.check_open_order():
                refresh = True

        self.order_manager.record_orders(closed_orders, refresh)

        if self.reconciler is not None:
            self.reconciler.reconcile(self._expected_position_qty())

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

        refresh_orders = False
        closed_orders: list[Order] = []
        for order in self.order_manager.orders:
            if order.quantity == 0 or order.phase == OrderPhase.CLOSED:
                continue

            if order.phase == OrderPhase.EXIT_PENDING:
                assert order.exit_id is not None
                assert order.exit_price is not None
                # 检查止盈单是否成交
                if latest.low <= order.exit_price <= latest.high:
                    exit_order_query_result = self.ex_client.query_order(
                        order.exit_id, self.config.symbol
                    )
                    if exit_order_query_result and OrderStatus.is_closed(
                        exit_order_query_result["status"]
                    ):
                        self._retire(order)
                        closed_orders.append(order)
                continue

            if order.phase == OrderPhase.ENTRY_PENDING:
                # 检查入场单是否成交
                if OrderStatus.is_open(order.status):
                    entry_order_query_result = self.ex_client.query_order(
                        order.entry_id, self.config.symbol
                    )
                    if entry_order_query_result:
                        order.status = entry_order_query_result["status"]
                if not OrderStatus.is_closed(order.status):
                    continue
                order.phase = OrderPhase.HOLDING
                refresh_orders = True

            # HOLDING: 入场已成交且未挂止盈单, 挂出实时止盈单
            exit_order_side = self.config.master_side.reversal()
            exit_order_id = build_order_id(exit_order_side)
            exit_price = order.price * (
                1 + self.config.master_side.to_int() * self.config.fixed_take_profit_rate
            )
            exit_qty = order.quantity * self.config.close_position_ratio

            try:
                exit_order_result = self.place_order(
                    exit_order_id,
                    exit_order_side,
                    exit_qty,
                    exit_price,
                    first_price=exit_price,
                )
            except Exception:
                # 提交结果未知: 记录已知ID转 EXIT_PENDING, 状态机核实 (custom_id 全链路复用)
                logger.exception(
                    "止盈单提交结果未知, 待状态机核实: %s", exit_order_id
                )
                order.exit_id = exit_order_id
                order.exit_price = exit_price
                order.phase = OrderPhase.EXIT_PENDING
                refresh_orders = True
                continue

            client_order_id = (
                exit_order_result.get("clientOrderId") if exit_order_result else None
            ) or exit_order_id
            order.exit_id = client_order_id
            order.exit_price = exit_price
            order.phase = OrderPhase.EXIT_PENDING
            refresh_orders = True

        self.order_manager.record_orders(closed_orders, refresh_orders=refresh_orders)
