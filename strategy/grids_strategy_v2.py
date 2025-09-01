import os
import secrets
from typing import List
from client.ex_client import ExSwapClient
from strategy import StrategyV2
from strategy import OrderSide
import logging
from pydantic import BaseModel
from model import Symbol
from strategy import Signal
import builtins

logger = logging.getLogger(__name__)

def build_order_id(side: OrderSide):
    return f'{side.value}{secrets.token_hex(nbytes=5)}'


def parse_orders(order_file_path):
    if os.path.exists(order_file_path):
        with open(order_file_path, 'r') as file:
            orders_data = json_util.loads(file.read())
            version = orders_data['version']
            orders = []
            for order in orders_data['orders']:
                try:
                    side = OrderSide(order['side'])
                except ValueError:
                    raise ValueError(f"Invalid order side: {order['side']}")
                orders.append(Order(
                    custom_id=order['custom_id'],
                    side=side,
                    price=order['price'],
                    quantity=order['quantity'],
                    take_profit_rate=order['take_profit_rate'],
                ))
        return {'version': version, 'orders': orders}
    else:
        return None

class Order(BaseModel):
    custom_id: str
    side: OrderSide
    price: float
    quantity: float
    take_profit_rate: float
    min_profit_rate: float = 0.002

    def total_value(self):
        return self.price * self.quantity

    # profit_level：表示盈利级别，值为 -1不可盈利，0损失手续费，1可盈利，2达到止盈标准
    def profit_level(self, current_price) -> int:
        compare_fun = builtins.float.__gt__
        if self.side == OrderSide.SELL:
            compare_fun = builtins.float.__lt__

        if compare_fun(current_price, self.take_profit_price()):
            return 2
        elif compare_fun(current_price, self.breakeven_price()):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    # 盈亏率
    def profit_and_loss_ratio(self, current_price):
        loss_rate = float("{:.6f}".format(abs(current_price - self.price) / self.price))
        if self.profit_level(current_price) < 0:
            return -loss_rate
        else:
            return loss_rate

    def _profit(self, rate):
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)

    def take_profit_price(self):
        return self._profit(self.take_profit_rate)

    def breakeven_price(self):
        return self._profit(self.min_profit_rate)

class OrderRecorder:
    def __init__(self, order_file_path: str):
        self.order_file_path = order_file_path
        self.orders = []
        self.reload = {
            "is_reload": False,
            "msg": ""
        }
        self.metrics = {
            # 历史订单
            "history_orders": [],
            # 盈亏
            "total_profit": 0,
            # 持仓量
            "position_qty": 0,
            # 持仓均价
            "position_avg_price": 0,
            # 持仓成本
            "position_cost": 0
        }

class SignalGridStrategyConfig(BaseModel):
    ex_client: ExSwapClient
    order_recorder: OrderRecorder
    symbol: Symbol
    position_side: str = 'long'
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    grid_spacing_rate: float = 0.0012
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    enable_exit_signal: bool = False
    signal: Signal | None = None
    signal_min_take_profit_rate: float = 0.002

    enable_fixed_profit_taking: bool = False
    fixed_take_profit_rate: float = 0.006

    place_order_type: str = 'chaser'


class SignalGridStrategy(StrategyV2):

    def __init__(self, config: SignalGridStrategyConfig):
        super().__init__()
        self.config = config
        self.orders = []

    def place_order(self, order_id: str, side: OrderSide, qty: float, price: float | None = None):
        self.config.ex_client.place_order(order_id, self.config.symbol.binance(), side.value, self.config.position_side, qty, price)
    
    def check_open_order(self) -> bool:

        # 检查订单是否到达上限
        if len(self.orders) >= self.config.max_order:
            return False
        
        # 检查是否有入场信号
        if self.config.signal:
            if not self.config.signal.is_entry(self.klines):
                return False
        
        # 检查当前价格是否在可交易的价格区间
        close_price = self.last_kline.close
        if close_price < self.config.lowest_price or close_price > self.config.highest_price:
            return False
        
        if self.config.master_side == OrderSide.BUY:
            recent_price_order = min(self.orders, key=lambda order: order.price)
        else:
            recent_price_order = max(self.orders, key=lambda order: order.price)

        if (not recent_price_order) or (recent_price_order and recent_price_order.profit_and_loss_ratio(close_price) <= -self.config.grid_spacing_rate): 
            order_id = build_order_id(self.config.master_side)
            order = Order(
                custom_id=order_id, 
                side=self.config.master_side, 
                price=close_price, 
                quantity=self.config.per_order_qty,
                take_profit_rate=self.config.fixed_take_profit_rate, 
                min_profit_rate=self.config.signal_min_take_profit_rate
            )
            self.orders.append(order)
            self.place_order(order_id, self.config.master_side, self.config.per_order_qty)
            return True
        return False

    def check_close_order(self):
        exit_signal = self.config.enable_exit_signal and self.config.signal and self.config.signal.is_exit(self.klines)

        new_orders: List[Order] = []
        flat_qty = 0
        for order in self.orders:
            profit_level = order.profit_level(self.last_kline.close)
            if (profit_level == 2 and self.config.enable_fixed_profit_taking) or (profit_level == 1 and exit_signal):
                flat_qty += order.quantity
            else:
                new_orders.append(order)

        if flat_qty > 0:
            flat_order_side = self.config.master_side.reversal()
            flat_order_id = build_order_id(flat_order_side)
            self.place_order(flat_order_id, flat_order_side, flat_qty)
        
        # 即使flat_qty==0，也需要更新订单，因为可以移除手动修改的0数量订单
        if len(self.orders) != len(new_orders):
            self.orders = new_orders
            # logger.info(f'{self.symbol} {json_util.dumps(self.orders)}')
            # json_util.dump_file({'version': self.config.version,'orders': self.orders}, self.config.order_file_path)

    def on_kline_finished(self):
        if not self.check_open_order():
            self.check_close_order()
