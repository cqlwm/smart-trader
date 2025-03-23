from abc import ABC, abstractmethod


class ExClient(ABC):
    @abstractmethod
    def balance(self, coin):
        pass

    @abstractmethod
    def cancel(self, custom_id, symbol):
        pass

    @abstractmethod
    def query_order(self, custom_id, symbol):
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
