import time

import ccxt
from typing import Any, Optional

from client.ex_client import ExSwapClient, ExSpotClient
from model import Order, OrderSide, PositionSide, OrderStatus, Symbol
from persistence.order_repository import InMemoryOrderRepository, OrderRepository


class BybitSwapClient(ExSwapClient):
    def __init__(self, _api_key, _api_secret, test, order_repo: OrderRepository | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.client = ccxt.bybit({
            'apiKey': _api_key,
            'secret': _api_secret,
        })
        if test:
            self.client.enable_demo_trading(test)

    def balance(self, coin):
        balance = self.client.fetch_balance(params={'accountType': 'UNIFIED'})
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol: Symbol) -> Order | None:
        raise NotImplementedError()

    def query_order(self, custom_id, symbol: Symbol) -> Order | None:
        raise NotImplementedError()

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
        position_side = kwargs.get('position_side', PositionSide.LONG)
        if isinstance(position_side, str):
            position_side = PositionSide(position_side)

        if position_side.value == 'long':
            position_idx = 1
        elif position_side.value == 'short':
            position_idx = 2
        else:
            raise NotImplementedError

        params = {
            'category': 'linear',
            'positionIdx': position_idx,
            'orderLinkId': custom_id,
            'marketUnit': 'baseCoin'
        }
        raw_order = self.client.create_order(
            symbol=symbol.ccxt(),
            type='limit' if price else 'market',
            side=order_side.value,
            amount=quantity,
            price=price,
            params=params
        )
        now = int(time.time() * 1000)
        order_type = 'limit' if price else 'market'
        order = Order(
            order_id=raw_order.get('clientOrderId', custom_id),
            strategy_id=strategy_id,
            symbol=symbol,
            side=order_side,
            position_side=position_side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=now,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self.order_repo.save(order)
        return order

    def close_position(self, symbol, position_side, auto_cancel=True):
        raise NotImplementedError()

    def positions(self, symbol=None):
        raise NotImplementedError()


class BybitSpotClient(ExSpotClient):
    def __init__(self, _api_key, _api_secret, test: bool = False):
        self.client = ccxt.bybit({
            'apiKey': _api_key,
            'secret': _api_secret,
        })
        if test:
            self.client.enable_demo_trading(test)

    def balance(self, coin):
        balance = self.client.fetch_balance(params={'accountType': 'UNIFIED'})
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol):
        return self.client.cancel_order('', symbol, params={'category': "spot", 'orderLinkId': custom_id})

    def query_order(self, custom_id, symbol: str):
        order_info = self.client.private_get_v5_order_realtime({
            'category': "spot",
            'symbol': symbol.replace('/', ''),
            'orderLinkId': custom_id,
            'openOnly': 1
        })
        if len(order_info['result']) == 0:
            return None
        ret = {}
        order = order_info['result']['list'][0]
        ret['state'] = 'closed' if order['orderStatus'] == 'Filled' else 'open'
        return ret

    def place_order(self, custom_id, symbol, order_side, quantity, price=None):
        order = self.client.create_order(
            symbol=symbol,
            type='limit' if price else 'market',
            side=order_side,
            amount=quantity,
            price=price,
            params={'orderLinkId': custom_id, 'marketUnit': 'baseCoin', 'category': "spot"}
        )
        return order
