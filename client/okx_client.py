import time

import ccxt
import threading
from ccxt.base.types import OrderType, OrderSide
from typing import Any, Optional

from client.ex_client import ExSwapClient, ExSpotClient
from model import Order, OrderSide as MOrderSide, PositionSide, OrderStatus, Symbol
from persistence.order_repository import InMemoryOrderRepository, OrderRepository
import log

logger = log.getLogger(__name__)

symbol_locks = {
    'LOCK': threading.Lock(),
}

def _get_lock(symbol):
    if symbol not in symbol_locks:
        lock = symbol_locks['LOCK']
        try:
            if lock.acquire():
                if symbol not in symbol_locks:
                    symbol_locks[symbol] = threading.Lock()
        finally:
            lock.release()
    return symbol_locks[symbol]

class OkxSwapClient(ExSwapClient):
    def __init__(self, api_key, secret, password, test: bool = False, order_repo: OrderRepository | None = None, data_store: Any | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store
        self.exchange = ccxt.okx({
            'apiKey': api_key,
            'secret': secret,
            'password': password,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
                'sandboxMode': test,
            },
            'headers': {
                'x-simulated-trading': '1' if test else '0',
            },
        })

    def balance(self, coin: str):
        balance = self.exchange.fetch_balance()
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol: Symbol) -> Order | None:
        self.exchange.cancel_order(id='', symbol=symbol.ccxt(), params={
            'clOrdId': custom_id
        })
        return self.order_repo.find_by_id(custom_id)

    def query_order(self, custom_id, symbol: Symbol) -> Order | None:
        return self.order_repo.find_by_id(custom_id)

    def place_order_v2(
        self,
        strategy_id: str,
        custom_id: str,
        symbol: Symbol,
        order_side: MOrderSide,
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Optional[Order]:
        position_side = kwargs.get('position_side', PositionSide.LONG)
        if isinstance(position_side, str):
            position_side = PositionSide(position_side)

        order_type = 'limit' if price else 'market'
        now = int(time.time() * 1000)

        if price is None:
            symbol_lock = _get_lock(symbol.ccxt())
            if symbol_lock.acquire(timeout=2):
                try:
                    o = self.exchange.private_post_trade_order_algo(params={
                        'algoClOrdId': custom_id,
                        "instId": symbol.ccxt(),
                        'side': order_side.value,
                        'posSide': position_side.value,
                        'sz': quantity,
                        'tdMode': 'cross',
                        'ordType': 'chase',
                    })
                    chase_order_scode = o['data'][0]['sCode']

                    time.sleep(1)
                    while True:
                        o_info = self.exchange.private_get_trade_order_algo(params={'algoClOrdId': custom_id})
                        state = o_info['data'][0]['state']
                        if state not in ('live', 'partially_effective'):
                            break
                        time.sleep(0.1)
                except Exception as e:
                    logger.error('chase order scode: %s, e: %s', chase_order_scode, str(e))  # type: ignore[name-defined]
                    if chase_order_scode == '0':  # type: ignore[name-defined]
                        return None
                finally:
                    symbol_lock.release()

        params = {'clOrdId': custom_id, 'positionSide': position_side.value}
        self.exchange.create_order(
            symbol=symbol.ccxt(),
            type='market' if price is None else 'limit',
            side=order_side.value,
            amount=quantity,
            price=price,
            params=params
        )

        order = Order(
            order_id=custom_id,
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
        positions = self.positions(symbol)
        for pos in positions:
            if pos['posSide'] == position_side:
                order_side = 'sell' if pos['side'] == 'buy' else 'buy'
                self.place_order_v2(
                    strategy_id="",
                    custom_id=f'close-{symbol}-{position_side}',
                    symbol=Symbol(base=symbol[:3], quote=symbol[3:]),
                    order_side=MOrderSide(order_side),
                    quantity=pos['quantity'],
                    position_side=position_side,
                )
        if auto_cancel:
            open_orders = self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                self.exchange.cancel_order(order['id'], symbol)

    def positions(self, symbol=None):
        positions = self.exchange.fetch_positions([symbol])
        return positions


class OkxSpotClient(ExSpotClient):
    def __init__(self, api_key, secret, password, test: bool = False):
        self.exchange = ccxt.okx({
            'apiKey': api_key,
            'secret': secret,
            'password': password,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'sandboxMode': test,
            },
            'headers': {
                'x-simulated-trading': '1' if test else '0',
            },
        })

    def balance(self, coin: str):
        balance = self.exchange.fetch_balance()
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol):
        return self.exchange.cancel_order(id='', symbol=symbol, params={
            'clOrdId': custom_id
        })

    def query_order(self, custom_id, symbol):
        return self.exchange.fetch_order(id='', symbol=symbol, params={
            'clOrdId': custom_id
        })

    def place_order(self, custom_id, symbol, order_side: OrderSide, quantity, price=None):
        order_type: OrderType = 'limit' if price else 'market'
        params = {
            'ordType': order_type,
            'clOrdId': custom_id,
        }
        if not price:
            params['tgtCcy'] = 'base_ccy'

        return self.exchange.create_order(symbol, order_type, order_side, quantity, price, params)
