import ccxt
from ex_client import ExSwapClient


class MexcSwapClient(ExSwapClient):
    def __init__(self):
        pass

    def balance(self, coin):
        pass

    def cancel(self, custom_id, symbol):
        pass

    def query_order(self, custom_id, symbol):
        pass

    def place_order(self, custom_id, symbol, order_side, position_side, quantity, price=None):
        pass

    def close_position(self, symbol, position_side, auto_cancel=True):
        pass

    def positions(self, symbol=None):
        pass
