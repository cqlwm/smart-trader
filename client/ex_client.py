from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from model import Symbol, SymbolInfo
from ccxt.base.exchange import Exchange

from model import OrderSide



class ExClient(ABC):
    exchange_name: str
    exchange: Exchange

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        raise NotImplementedError()

    @abstractmethod
    def balance(self, coin: str) -> float:
        pass

    @abstractmethod
    def cancel(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        pass

    @abstractmethod
    def query_order(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        pass

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100) -> List[List[Any]]:
        return self.exchange.fetch_ohlcv(symbol.ccxt(), timeframe, limit=limit)
    
    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide, quantity: float, price: Optional[float] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        '''
        kwargs: 
            position_side
            time_in_force
        '''
        pass

class ExSwapClient(ExClient):

    @abstractmethod
    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        pass

    @abstractmethod
    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        pass


class ExSpotClient(ExClient):
    @abstractmethod
    def place_order(self, custom_id, symbol, order_side, quantity, price=None):
        pass
