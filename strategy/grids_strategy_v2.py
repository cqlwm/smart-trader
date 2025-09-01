import os
import secrets
from abc import abstractmethod
from typing import List
from pandas import DataFrame
from client.ex_client import ExSwapClient, ExSpotClient
from strategy import Strategy
from strategy import Order, OrderSide
from utils import log, json_util

logger = log.build_logger('grids_strategy')


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

'''
{
    "relaod": {
        "is_reload": true,
        "msg": ""
    },
    "orders": [
        {
            "custom_id": "1",
            "side": "buy",
            "price": 100,
            "quantity": 1,
            "take_profit_rate": 0.006
        }
    ],
    "metrics": {
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
}
'''
class OrderFile:
    def __init__(self, version: int, orders: List[Order]):
        self.version = version
        self.orders = orders

class GridStrategy(Strategy):

    def __init__(self, ex_client: ExSwapClient, symbol: str, position_side: str, master_side: OrderSide,
                 strategy_key: str,
                 per_qty: float = 0.02, take_profit_rate=0.006, upper_limit: float = 1000000, lower_limit: float = 0):
        super().__init__()
        self.ex_client = ex_client
        self.position_side = position_side
        self.symbol = symbol
        self.master_side = master_side
        self.per_qty = per_qty
        self.take_profit_rate = take_profit_rate
        self.upper_limit = upper_limit
        self.lower_limit = lower_limit
        self.signal = None
        self.enable_exit_signal = False
        self.fixed_profit_taking = True
        self.min_profit_rate = 0.002
        self.grid_gap_rate = 0.0012
        self.max_order = 10000

        self.orders: List[Order] = []
        self.version = 1
        self.order_file_path = f'{strategy_key}.json'

        file_orders = parse_orders(self.order_file_path)
        if file_orders:
            self.version = file_orders['version']
            self.orders = file_orders['orders']

    def place_order(self, order_id: str, side: OrderSide, qty: float, price: float = None):
        self.ex_client.place_order(order_id, self.symbol, side.value, self.position_side, qty, price)

    def run(self, kline: DataFrame):
        file_order_data = parse_orders(self.order_file_path)
        if file_order_data:
            file_version = file_order_data['version']
            if file_version > self.version:
                self.version = file_version
                self.orders = file_order_data['orders']

        close_price = kline.iloc[-1]['close']
        can_place_order = len(self.orders) < self.max_order
        entry_signal = self.signal.is_entry(kline)
        within_price_range = self.upper_limit >= close_price >= self.lower_limit

        if can_place_order and entry_signal and within_price_range:
            last_order = self.orders[-1] if self.orders else None
            if (not last_order) or (last_order and last_order.loss_rate(close_price) >= self.grid_gap_rate):
                order_id = build_order_id(self.master_side)
                order = Order(custom_id=order_id, side=self.master_side, price=close_price, quantity=self.per_qty,
                              take_profit_rate=self.take_profit_rate, min_profit_rate=self.min_profit_rate)
                self.orders.append(order)
                self.place_order(order_id, self.master_side, self.per_qty)
                return

        exit_signal = self.enable_exit_signal and self.signal.is_exit(kline)

        new_orders: List[Order] = []
        flat_qty = 0
        for order in self.orders:
            profit_level = order.profit_level(close_price)
            if (profit_level == 2 and self.fixed_profit_taking) or (profit_level == 1 and exit_signal):
                flat_qty += order.quantity
            else:
                new_orders.append(order)

        if flat_qty > 0:
            flat_order_side = self.master_side.reversal()
            flat_order_id = build_order_id(flat_order_side)
            self.place_order(flat_order_id, flat_order_side, flat_qty)
        self.orders = new_orders

        logger.info(f'{self.symbol} {json_util.dumps(self.orders)}')
        json_util.dump_file({'version': self.version,'orders': self.orders}, self.order_file_path)
