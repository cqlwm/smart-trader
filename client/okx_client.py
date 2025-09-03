import time

import ccxt
import threading
from ccxt.base.types import OrderType, OrderSide

from client.ex_client import ExSwapClient, ExSpotClient
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
    def __init__(self, api_key, secret, password, test: bool = False):
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
        # self.exchange.private_post_account_set_position_mode({'posMode': 'long_short_mode'})

    def balance(self, coin: str):
        balance = self.exchange.fetch_balance()
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol):
        return self.exchange.cancel_order(id='', symbol=symbol, params={
            'clOrdId': custom_id
        })

    def query_order(self, custom_id, symbol):
        res = {}
        order = self.exchange.fetch_order(id='', symbol=symbol, params={
            'clOrdId': custom_id
        })
        res['state'] = 'closed' if order['info']['state'] == 'filled' else 'open'
        return res

    def place_order(self, custom_id, symbol, order_side, position_side, quantity, price=None):
        if price is None:

            symbol_lock = _get_lock(symbol)

            chase_order_scode = '0'
            if symbol_lock.acquire(timeout=2):
                try:
                    # 等待2秒，2秒内能拿到锁就执行追逐单
                    o = self.exchange.private_post_trade_order_algo(params={
                        'algoClOrdId': custom_id,
                        "instId": symbol,
                        'side': order_side,
                        'posSide': position_side,
                        'sz': quantity,
                        'tdMode': 'cross',
                        # chase: 追逐限价委托，仅适用于交割和永续
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

                    return
                except Exception as e:
                    logger.error(f'chase order scode: {chase_order_scode}, e: {str(e)}')
                    if chase_order_scode == '0':
                        return
                finally:
                    symbol_lock.release()

        params = {'clOrdId': custom_id, 'positionSide': position_side}
        self.exchange.create_order(
            symbol=symbol,
            type='market' if price is None else 'limit' ,
            side=order_side,
            amount=quantity,
            price=price,
            params=params
        )

    def close_position(self, symbol, position_side, auto_cancel=True):
        positions = self.positions(symbol)
        for pos in positions:
            if pos['posSide'] == position_side:
                # Close position by placing an opposite order
                order_side = 'sell' if pos['side'] == 'buy' else 'buy'
                self.place_order(f'close-{symbol}-{position_side}', symbol, order_side, position_side, pos['quantity'])
        if auto_cancel:
            # Cancel all open orders related to the symbol
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

    # return status=open,closed
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

