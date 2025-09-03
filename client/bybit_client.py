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
