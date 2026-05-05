import time
from typing import Any, Optional

from client.ex_client import ExSwapClient
from model import Order, OrderSide, PositionSide, OrderStatus, Symbol
from persistence.order_repository import InMemoryOrderRepository, OrderRepository


class MexcSwapClient(ExSwapClient):
    def __init__(self, order_repo: OrderRepository | None = None, data_store: Any | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store

    def balance(self, coin):
        pass

    def cancel(self, custom_id: str, symbol: Symbol) -> Order | None:
        pass

    def query_order(self, custom_id: str, symbol: Symbol) -> Order | None:
        pass

    def place_order_v2(
        self,
        strategy_id: str,
        custom_id: str,
        symbol: Symbol,
        order_side: OrderSide,
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Optional[Order]:
        pass

    def close_position(self, symbol, position_side, auto_cancel=True):
        pass

    def positions(self, symbol=None):
        pass
