import os
import secrets
import threading
from typing import Any, List, Callable, Dict
from client.ex_client import ExSwapClient
from strategy import SingleTimeframeStrategy
from model import OrderSide, OrderStatus, PlaceOrderBehavior, PositionSide
import logging
from pydantic import BaseModel, ConfigDict
from model import Symbol
from strategy import Signal

logger = logging.getLogger(__name__)

def build_order_id(side: OrderSide):
    return f'{side.value}{secrets.token_hex(nbytes=5)}'

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

    def __eq__(self, other: Any):
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
        loss_rate = float("{:.6f}".format(abs(current_price - self.price) / self.price))
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
    '''
    订单记录器
    @param order_file_path 订单文件路径
    @param orders 当前订单
    @param history_orders 历史订单
    @param is_reload 程序中通常不会直接设置该值,而是用户在需要重新加载时在本地备份文件中设置True,实现热更新的效果
    @param reload_msg 重新加载消息
    @param total_profit 总利润
    '''
    order_file_path: str
    orders: List[Order] = []
    history_orders: List[Order] = []
    is_reload: bool = False

    def record(self, latest_orders: List[Order], closed_orders: List[Order], refresh_orders: bool = False):

        self.orders = latest_orders
        refresh_orders = refresh_orders or len(latest_orders) != len(self.orders)

        if closed_orders:
            self.history_orders += closed_orders
            refresh_orders = True

        if refresh_orders and self.order_file_path:
            with open(self.order_file_path, 'w') as f:
                f.write(self.model_dump_json())

    def check_reload(self, force: bool = False) -> List[Order] | None:
        '''
        从本地文件中读取订单，并检查是否需要重新加载
        @param force 强制重新加载
        '''
        if self.order_file_path and os.path.exists(self.order_file_path):
            with open(self.order_file_path, 'r') as f:
                _recorder = OrderRecorder.model_validate_json(f.read())
                if _recorder.is_reload or force:
                    logger.info(f"Reload orders from {self.order_file_path}, force={force}")
                    return _recorder.orders
        return None

class OrderManager:
    """线程安全的订单管理器"""

    def __init__(self, order_file_path: str):
        self._orders: Dict[str, Order] = {}
        self._lock = threading.RLock()
        self._order_recorder = OrderRecorder(order_file_path=order_file_path)

    @property
    def orders(self) -> List[Order]:
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

    def record_orders(self, closed_orders: List[Order] = [], refresh_orders: bool = False) -> None:
        """
        记录订单到文件, 如果closed_orders为空, 则只记录当前订单
        @param closed_orders 已经关闭订单
        @param refresh_orders 刷新到文件
        """
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

    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN  # 下单行为

    order_file_path: str = 'data/grids_strategy_v2.json'
    
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

    def place_order(self, order_id: str, side: OrderSide, qty: float, price: float, first_price: float | None = None):
        if self.config.position_reverse:
            position_side = PositionSide.SHORT if self.config.position_side == PositionSide.LONG else PositionSide.LONG
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        else:
            position_side = self.config.position_side

        return self.ex_client.place_order_v2(
            custom_id=order_id, 
            symbol=self.config.symbol, 
            order_side=side, 
            quantity=qty, 
            price=price, # place_order_behavior == NORMAL 价格才会生效
            position_side=position_side,
            place_order_behavior=self.config.place_order_behavior, 
            first_price=first_price
        )

    def _check_max_order_stop_loss(self) -> bool:
        if self.config.enable_max_order_stop_loss and self.config.max_order - len(self.order_manager.orders) <= 1:
            return True
        return False

    def check_open_order(self) -> bool:
        # 检查订单是否到达上限
        if len(self.order_manager.orders) >= self.config.max_order:
            return False

        # 检查是否有入场信号
        if self.config.signal:
            if not self.config.signal.is_entry(self.klines_df):
                return False

        # 检查当前价格是否在可交易的价格区间
        if self.latest_kline_obj is None:
            return False
        
        close_price = self.latest_kline_obj.close
        if close_price < self.config.lowest_price or close_price > self.config.highest_price:
            return False

        if self.order_manager.orders:
            recent_price_order = self.config.master_side.extremum_fun()(self.order_manager.orders, key=lambda order: order.price)
        else:
            recent_price_order = None

        if (not recent_price_order) or (recent_price_order and recent_price_order.profit_and_loss_ratio(close_price) <= -self.config.grid_spacing_rate):
            if self._check_max_order_stop_loss():
                return False
            order_id = build_order_id(self.config.master_side)
            # Initialize stop loss settings
            stop_loss_rate = self.config.order_stop_loss_rate if self.config.enable_order_stop_loss else 0.0
            trailing_stop_rate = self.config.trailing_stop_rate if self.config.enable_trailing_stop else 0.0
            trailing_activation_rate = self.config.trailing_stop_activation_profit_rate if self.config.enable_trailing_stop else 0.0

            # Calculate initial stop price
            current_stop_price = None
            if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
                if self.config.master_side == OrderSide.BUY:
                    current_stop_price = close_price * (1 - stop_loss_rate)
                else:  # SELL
                    current_stop_price = close_price * (1 + stop_loss_rate)

            order = Order(
                entry_id=order_id,
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
                current_stop_price=current_stop_price
            )

            
            if self.config.per_order_qty == 0:
                order.status = OrderStatus.CLOSED.value
            else:
                entry_order_result = self.place_order(order_id, self.config.master_side, self.config.per_order_qty, close_price, first_price=close_price)
                if entry_order_result and entry_order_result.get('clientOrderId'):
                    # 入场订单ID可能因为追单行为而改变,所以使用返回的订单ID
                    order.entry_id = entry_order_result['clientOrderId']
                    order.price = entry_order_result['price']
                    order.status = entry_order_result['status']
            self.order_manager.add_order(order)
            return True
        return False

    def check_close_order(self) -> List[Order]:
        if self.latest_kline_obj is None:
            return []
        
        current_orders = self.order_manager.orders
        exit_signal = self.config.enable_exit_signal and self.config.signal and self.config.signal.is_exit(self.klines_df)

        remove_orders: List[Order] = []
        exit_orders: List[Order] = []
        exit_qty = 0
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for order in current_orders:
            profit_level = order.profit_level(self.latest_kline_obj.close)

            # 检查止损条件
            stop_loss_triggered = False
            if order.enable_stop_loss and order.current_stop_price is not None:
                if order.side == OrderSide.BUY:
                    stop_loss_triggered = self.latest_kline_obj.close <= order.current_stop_price
                else:  # SELL
                    stop_loss_triggered = self.latest_kline_obj.close >= order.current_stop_price

            if stop_loss_order_all or (profit_level == 2 and self.config.fixed_rate_take_profit) or (profit_level == 1 and exit_signal) or stop_loss_triggered:
                if OrderStatus.is_open(order.status):
                    entry_order_query_result = self.ex_client.query_order(order.entry_id, self.config.symbol)
                    order.status = OrderStatus.EXPIRED.value if entry_order_query_result is None else entry_order_query_result['status']
                    if not OrderStatus.is_closed(order.status):
                        remove_orders.append(order)
                        if OrderStatus.is_open(order.status):
                            self.ex_client.cancel(order.entry_id, self.config.symbol)
                        continue

                if order.exit_id and order.exit_price:
                    exit_order_query_result = self.ex_client.query_order(order.exit_id, self.config.symbol)
                    if exit_order_query_result:
                        exit_status = exit_order_query_result['status']
                        if OrderStatus.is_closed(exit_status):
                            remove_orders.append(order)
                            continue
                        elif OrderStatus.is_open(exit_status):
                            self.ex_client.cancel(order.exit_id, self.config.symbol)
                        else:
                            pass

                exit_qty += order.quantity
                order.exit_price = self.latest_kline_obj.close
                exit_orders.append(order)

        if exit_qty > 0:
            exit_order_side = self.config.master_side.reversal()
            exit_order_id = build_order_id(exit_order_side)
            actual_exit_qty = exit_qty * self.config.close_position_ratio
            execute_exit_order_result = self.place_order(exit_order_id, exit_order_side, actual_exit_qty, self.latest_kline_obj.close)
            if execute_exit_order_result:
                for order in exit_orders:
                    order.exit_id = execute_exit_order_result['clientOrderId']
                    order.exit_price = execute_exit_order_result['price']

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return exit_orders + remove_orders

    def _on_kline_finished(self):
        if not self.is_running or self.latest_kline_obj is None:
            return

        # 检查是否需要重新加载订单
        refresh = self.order_manager.load_orders()

        # 更新跟踪止损
        extremum_price = self.latest_kline_obj.high if self.config.master_side == OrderSide.BUY else self.latest_kline_obj.low
        current_orders = self.order_manager.orders
        for order in current_orders:
            if not order.enable_trailing_stop or order.current_stop_price is None:
                continue

            # 检查是否达到激活盈利条件
            activation_price = order.price * (1 + order.trailing_stop_activation_profit_rate * order.side.to_int())
            if order.side.compare_fun(and_eq=True)(extremum_price, activation_price):
                new_stop_price = extremum_price * (1 - order.trailing_stop_rate * order.side.to_int())
                order.current_stop_price = order.side.reversal().extremum_fun()(order.current_stop_price, new_stop_price)
                refresh = True

        if not self.check_open_order():
            closed_orders = self.check_close_order()
        else:
            closed_orders = []
            refresh = True

        self.order_manager.record_orders(closed_orders, refresh)

    def _on_kline(self):
        if self.latest_kline_obj is None:
            return
        
        orders_to_process = self.order_manager.orders
        refresh_orders = False

        # 检查是否需要触发实时止盈订单
        closed_orders = []
        if self.config.fixed_rate_take_profit and self.config.take_profit_use_limit_order:
            for order in orders_to_process:
                if order.quantity == 0:
                    continue
                
                if order.exit_id and order.exit_price:
                    # 检查退出是否成交
                    if self.latest_kline_obj.low <= order.exit_price <= self.latest_kline_obj.high:
                        exit_order_query_result = self.ex_client.query_order(order.exit_id, self.config.symbol)
                        if exit_order_query_result:
                            exit_status = exit_order_query_result['status']
                            if OrderStatus.is_closed(exit_status):
                                closed_orders.append(order)
                            elif OrderStatus.is_open(exit_status):
                                pass # 等待订单成交
                            else:
                                logger.warning(f'Unknown exit order status: {exit_status} for order: {order}')
                else:
                    # 检查进入订单是否成交
                    if OrderStatus.is_open(order.status):
                        entry_order_query_result = self.ex_client.query_order(order.entry_id, self.config.symbol)
                        if entry_order_query_result:
                            order.status = entry_order_query_result['status']
                    
                    # 如果进入订单成交，触发实时止盈订单
                    if OrderStatus.is_closed(order.status):
                        exit_order_side = self.config.master_side.reversal()
                        exit_order_id = build_order_id(exit_order_side)
                        exit_price = order.price * (1 + self.config.master_side.to_int() * self.config.fixed_take_profit_rate)
                        exit_qty = order.quantity * self.config.close_position_ratio

                        exit_order_result = self.place_order(exit_order_id, exit_order_side, exit_qty, exit_price, first_price=exit_price)
                        if exit_order_result and exit_order_result.get('clientOrderId'):
                            order.exit_id = exit_order_result['clientOrderId']
                            order.exit_price = exit_price
                            refresh_orders = True

            self.order_manager.record_orders(closed_orders, refresh_orders=refresh_orders)
