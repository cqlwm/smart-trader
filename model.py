from datetime import datetime
from pydantic import BaseModel
from enum import Enum


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

class Symbol(BaseModel):
    base: str
    quote: str

    def __init__(self, base: str, quote: str):
        super().__init__(base=base.upper(), quote=quote.upper())

    def ccxt(self):
        return f'{self.base}/{self.quote}'

    def binance(self):
        return f'{self.base}{self.quote}'
    
    def binance_ws_sub_kline(self, timeframe: str):
        return f'{self.binance()}@kline_{timeframe}'.lower()
    
    def to_str(self, exchange_name: str):
        if exchange_name == 'binance':
            return self.binance()
        else:
            return self.ccxt()

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
        
    def to_dict(self):
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