from datetime import datetime
from typing import Any
from pydantic import BaseModel
from enum import Enum
from dataclasses import dataclass
import builtins
from decimal import Decimal
class PositionSide(Enum):
    LONG = 'long'
    SHORT = 'short'

class OrderSide(Enum):
    BUY = 'buy'
    SELL = 'sell'

    def reversal(self):
        return OrderSide.SELL if self == OrderSide.BUY else OrderSide.BUY

# order status
        # statuses: dict = {
        #     'NEW': 'open',
        #     'PARTIALLY_FILLED': 'open',
        #     'ACCEPTED': 'open',
        #     'FILLED': 'closed',
        #     'CANCELED': 'canceled',
        #     'CANCELLED': 'canceled',
        #     'PENDING_CANCEL': 'canceling',  # currently unused
        #     'REJECTED': 'rejected',
        #     'EXPIRED': 'expired',
        #     'EXPIRED_IN_MATCH': 'expired',
        # }

class OrderStatus(Enum):
    OPEN = 'open'
    CLOSED = 'closed'
    CANCELED = 'canceled'
    REJECTED = 'rejected'
    EXPIRED = 'expired'

class PlaceOrderBehavior(Enum):
    CHASER = 'chaser'  # 追单且成交
    CHASER_OPEN = 'chaser_open'  # 追单只下单
    # LIMIT = 'limit'  # 限价单
    # MARKET = 'market'  # 市价单
    # 常规
    NORMAL = 'normal'  # 常规

class Symbol(BaseModel):
    base: str
    quote: str

    def ccxt(self):
        return f'{self.base}/{self.quote}'.upper()

    def binance(self):
        return f'{self.base}{self.quote}'.upper()
    
    def binance_ws_sub_kline(self, timeframe: str):
        return f'{self.binance()}@kline_{timeframe}'.lower()
    
    def simple(self):
        return f'{self.base}{self.quote}'
    
    def to_str(self, exchange_name: str | None = None):
        if exchange_name is None:
            return f'{self.base}{self.quote}'
        elif exchange_name == 'binance':
            return self.binance()
        else:
            return self.ccxt()

class SymbolInfo(BaseModel):
    symbol: Symbol
    tick_size: float
    min_price: float
    max_price: float
    step_size: float
    min_qty: float
    max_qty: float
    min_notional: float = 6.0

    def _precision(self, number: float | str):
        return Decimal(str(number)).normalize().as_tuple().exponent * -1
    
    def price_precision(self):
        return self._precision(self.tick_size)
    
    def qty_precision(self):
        return self._precision(self.step_size)

    def format_precision(self, value: float | str, precision: float | str):
        decimal_value = Decimal(str(value))
        format_str = f"{{:.{precision}f}}"
        return float(format_str.format(decimal_value))
    
    def format_price(self, price: float | str):
        return self.format_precision(price, self.price_precision())
    
    def format_qty(self, qty: float | str):
        return self.format_precision(qty, self.qty_precision())

class Kline:
    def __init__(self, symbol: Symbol, timeframe: str, open: float, high: float, low: float, close: float, volume: float, timestamp: int, finished: bool):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp
        self.datetime = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
        self.finished = finished
        
    def to_dict(self) -> dict[str, Any]:
        return {
            'datetime': self.datetime,
            # 'timestamp': self.timestamp,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'finished': self.finished
        }

@dataclass
class Order:
    # ID规则 side + 10random + [i]
    custom_id: str
    side: OrderSide
    price: float
    quantity: float
    take_profit_rate: float
    min_profit_rate: float = 0.002

    def total_value(self):
        return self.price * self.quantity

    # profit_level：表示盈利级别，值为 0损失手续费，-1不可盈利，1可盈利，2达到止盈标准
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

    def loss_rate(self, current_price):
        if self.profit_level(current_price) < 0:
            return float("{:.4f}".format(abs(current_price - self.price) / self.price))
        else:
            return 0

    def _profit(self, rate):
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)

    def take_profit_price(self):
        return self._profit(self.take_profit_rate)

    def breakeven_price(self):
        return self._profit(self.min_profit_rate)

    def exit_id(self, i: int | None = None):
        exit_id = self.custom_id.replace(self.side.value, self.side.reversal().value, 1)
        return exit_id if i is None else f'{exit_id}{i}'