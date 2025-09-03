from abc import ABC, abstractmethod
from typing import List

from model import Symbol
from ccxt.base.exchange import Exchange

from strategy import OrderSide



class ExClient(ABC):
    exchange_name: str
    exchange: Exchange

    @abstractmethod
    def balance(self, coin):
        pass

    @abstractmethod
    def cancel(self, custom_id, symbol):
        pass

    @abstractmethod
    def query_order(self, custom_id, symbol):
        pass

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100) -> List[list]:
        return self.exchange.fetch_ohlcv(symbol.ccxt(), timeframe, limit=limit)
    
    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide, quantity: float, price=None, **kwargs):
        pass

class ExSwapClient(ExClient):
    @abstractmethod
    def place_order(self, custom_id, symbol, order_side, position_side, quantity, price=None):
        pass

    @abstractmethod
    def close_position(self, symbol, position_side, auto_cancel=True):
        pass

    @abstractmethod
    def positions(self, symbol=None):
        pass


class ExSpotClient(ExClient):
    @abstractmethod
    def place_order(self, custom_id, symbol, order_side, quantity, price=None):
        pass
