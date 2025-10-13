import os
import secrets
from typing import Any, List
from client.ex_client import ExSwapClient
from strategy import StrategyV2
from model import OrderSide, PlaceOrderBehavior, PositionSide
import logging
from pydantic import BaseModel, ConfigDict
from model import Symbol
from strategy import Signal
import builtins

logger = logging.getLogger(__name__)

def build_order_id(side: OrderSide):
    return f'{side.value}{secrets.token_hex(nbytes=5)}'

class Order(BaseModel):
    custom_id: str
    side: OrderSide
    price: float
    quantity: float
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    close_price: float | None = None
    status: str | None = None

    def __hash__(self):
        return hash(self.custom_id)

    def __eq__(self, other: Any):
        if isinstance(other, Order):
            return self.custom_id == other.custom_id
        return False

    # profit_level：表示盈利级别，值为 -1不可盈利，0损失手续费，1可盈利，2达到止盈标准
    def profit_level(self, current_price: float) -> int:
        compare_fun = builtins.float.__gt__
        if self.side == OrderSide.SELL:
            compare_fun = builtins.float.__lt__

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
    @param is_reload 程序中通常不会直接设置该值, 而是在需要重新加载时在本地备份文件中设置True
    @param reload_msg 重新加载消息
    @param total_profit 总利润
    '''
    order_file_path: str
    orders: List[Order] = []
    history_orders: List[Order] = []
    is_reload: bool = False
    reload_msg: str = ""
    total_profit: float = 0

    def record(self, latest_orders: List[Order], close_orders: List[Order]):
        changed = False
        if len(latest_orders) != len(self.orders):
            self.orders = latest_orders.copy()
            changed = True

        if close_orders:
            self.history_orders += close_orders
            changed = True

        if changed:
            with open(self.order_file_path, 'w') as f:
                f.write(self.model_dump_json())

    def check_reload(self, force: bool = False) -> List[Order] | None:
        '''
        从本地文件中读取订单，并检查是否需要重新加载
        @param force 强制重新加载
        '''
        if not self.order_file_path:
            return None
        if not os.path.exists(self.order_file_path):
            return None
        with open(self.order_file_path, 'r') as f:
            _recorder = OrderRecorder.model_validate_json(f.read())
            if _recorder.is_reload or force:
                self.orders = _recorder.orders
                return self.orders.copy()
        return None

class SignalGridStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    symbol: Symbol
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    grid_spacing_rate: float = 0.01
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    enable_exit_signal: bool = False
    signal: Signal | None = None
    signal_min_take_profit_rate: float = 0.002

    enable_fixed_profit_taking: bool = False
    fixed_take_profit_rate: float = 0.006

    close_position_ratio: float = 1.0

    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN  # 下单行为

    order_file_path: str = 'data/grids_strategy_v2.json'
    
    # position_stop_loss_rate: float = 0.1

    position_reverse: bool = False  # 是否反向持仓

class SignalGridStrategy(StrategyV2):

    def __init__(self, config: SignalGridStrategyConfig, ex_client: ExSwapClient):
        super().__init__()
        self.config = config
        self.ex_client = ex_client
        self.order_recorder: OrderRecorder = OrderRecorder(order_file_path=self.config.order_file_path)
        self.orders: List[Order] = self.order_recorder.check_reload(force=True) or []

    def place_order(self, order_id: str, side: OrderSide, qty: float, price: float):
        if self.config.position_reverse:
            position_side = PositionSide.SHORT if self.config.position_side == PositionSide.LONG else PositionSide.LONG
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        else:
            position_side = self.config.position_side

        return self.ex_client.place_order_v2(custom_id=order_id, symbol=self.config.symbol, order_side=side, quantity=qty, price=price, position_side=position_side,
                                             place_order_behavior=self.config.place_order_behavior)


    def check_open_order(self) -> bool:

        # 检查订单是否到达上限
        if len(self.orders) >= self.config.max_order:
            return False
        
        # 检查是否有入场信号
        if self.config.signal:
            if not self.config.signal.is_entry(self.klines_to_dataframe()):
                return False
        
        # 检查当前价格是否在可交易的价格区间
        close_price = self.last_kline.close
        if close_price < self.config.lowest_price or close_price > self.config.highest_price:
            return False
        
        if self.orders:
            extremum_fun = min if self.config.master_side == OrderSide.BUY else max
            recent_price_order = extremum_fun(self.orders, key=lambda order: order.price)
        else:
            recent_price_order = None

        if (not recent_price_order) or (recent_price_order and recent_price_order.profit_and_loss_ratio(close_price) <= -self.config.grid_spacing_rate): 
            order_id = build_order_id(self.config.master_side)
            order = Order(
                custom_id=order_id, 
                side=self.config.master_side, 
                price=close_price, 
                quantity=self.config.per_order_qty,
                fixed_take_profit_rate=self.config.fixed_take_profit_rate, 
                signal_min_take_profit_rate=self.config.signal_min_take_profit_rate
            )
            self.orders.append(order)
            place_order_result = self.place_order(order_id, self.config.master_side, self.config.per_order_qty, close_price)
            if place_order_result:
                if place_order_result.get('clientOrderId'):
                    order.custom_id = place_order_result['clientOrderId']
                    order.price = place_order_result['price']
                    order.status = place_order_result['status']
            return True
        return False

    def check_close_order(self) -> List[Order]:
        exit_signal = self.config.enable_exit_signal and self.config.signal and self.config.signal.is_exit(self.klines_to_dataframe())

        new_orders: List[Order] = []
        flat_orders: List[Order] = []
        flat_qty = 0
        for order in self.orders:
            profit_level = order.profit_level(self.last_kline.close)
            if (profit_level == 2 and self.config.enable_fixed_profit_taking) or (profit_level == 1 and exit_signal):
                if order.status == 'open':
                    query_order = self.ex_client.query_order(order.custom_id, self.config.symbol)
                    if query_order and query_order['status'] != 'closed':
                        continue
                    else:
                        order.status = 'closed'
                flat_qty += order.quantity
                order.close_price = self.last_kline.close
                flat_orders.append(order)
            else:
                new_orders.append(order)

        if flat_qty > 0:
            flat_order_side = self.config.master_side.reversal()
            flat_order_id = build_order_id(flat_order_side)
            actual_flat_qty = flat_qty * self.config.close_position_ratio
            place_order_result = self.place_order(flat_order_id, flat_order_side, actual_flat_qty, self.last_kline.close)
            if place_order_result:
                for order in flat_orders:
                    order.close_price = place_order_result['price']
        
        # 即使flat_qty==0，也需要更新订单，因为可以移除手动修改的0数量订单
        if len(self.orders) != len(new_orders):
            self.orders = new_orders
        
        return flat_orders

    def on_kline_finished(self):
        self.orders = self.order_recorder.check_reload() or self.orders

        if not self.check_open_order():
            close_orders = self.check_close_order()
        else:
            close_orders = []

        self.order_recorder.record(self.orders, close_orders)