import ccxt

from client.ex_client import ExSwapClient, ExSpotClient


class BybitSwapClient(ExSwapClient):
    def __init__(self, _api_key, _api_secret, test):
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
        raise NotImplementedError()
        # return self.client.cancel_order(custom_id, symbol)

    def query_order(self, custom_id, symbol):
        raise NotImplementedError()
        # return self.client.fetch_order(custom_id, symbol)

    def place_order(self, custom_id, symbol, order_side, position_side, quantity, price=None):
        if position_side == 'long':
            position_idx = 1
        elif position_side == 'short':
            position_idx = 2
        else:
            raise NotImplementedError

        params = {
            'category': 'linear',
            'positionIdx': position_idx,
            'orderLinkId': custom_id,
            'marketUnit': 'baseCoin'
        }
        order = self.client.create_order(
            symbol=symbol,
            type='limit' if price else 'market',
            side=order_side,
            amount=quantity,
            price=price,
            params=params
        )
        return order

    def close_position(self, symbol, position_side, auto_cancel=True):
        raise NotImplementedError()
        # position = self.positions(symbol)
        # if position and position['positionSide'] == position_side:
        #     order_side = 'sell' if position_side == 'long' else 'buy'
        #     quantity = position['contracts']
        #     self.place_order(None, symbol, order_side, 'close', quantity)
        #     if auto_cancel:
        #         self.client.cancel_all_orders(symbol)
        # return True

    def positions(self, symbol=None):
        raise NotImplementedError()
        # positions = self.client.private_get_position_list()['result']
        # if symbol:
        #     return next((pos for pos in positions if pos['symbol'] == symbol), None)
        # return positions


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

    # status=open,closed
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


def _test_spot_api():
    # "https://api-demo.bybit.com"
    # ("qDYBIG8Itcwx8WJIjP", "06tz0gmTjfSSX2jgfPNcELkI3yj1m5kzDdvb", test = true)
    # ("gAejC8EyeylqZbC05a", "xl1HEJAOp7NVIFr0OQAYJW3ArBZTj3811dvE", test = false)
    # 示例用法
    api_key = 'qDYBIG8Itcwx8WJIjP'
    api_secret = '06tz0gmTjfSSX2jgfPNcELkI3yj1m5kzDdvb'

    spot_client = BybitSpotClient(api_key, api_secret, True)

    print(spot_client.balance('USDT'))

    custom_id = 'test_0727_03'
    symbol = 'BTC/USDT'

    # order = spot_client.place_order(custom_id, symbol, 'buy', 0.01, 67800)
    # print(order)
    # order_q = spot_client.query_order(custom_id, symbol)
    # print(order_q)
    # order = spot_client.cancel(custom_id, symbol)
    # print(order)

def _test_swap_api():
    # test
    # qxKpCbMO9h2ZXqwN7A
    # VzRKn1Mhb1LgtfMF7se7wEsU9WHQ4K2NkObl
    api_key = 'g0TfHnDyxZOWeHLkU5'
    api_secret = 'ZobzYT4Wr70mCPdRDMcCPPAvMID5o8r3e5X7'
    swap_client = BybitSwapClient(api_key, api_secret, True)
    print(swap_client.balance('USDT'))

    o = swap_client.place_order('test-241218-05', 'SOL/USDT', 'sell', 'short', 1, 218)
    print(o)

if __name__ == '__main__':
    print('hi')
    _test_swap_api()
