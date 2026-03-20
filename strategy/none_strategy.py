from client.ex_client import ExClient
from model import Symbol
from strategy import SimpleStrategy


class NoneStrategy(SimpleStrategy):
    """空策略，不做任何操作"""
    def __init__(self, symbol: Symbol, timeframe: str, ex_client: ExClient):
        super().__init__(symbol, timeframe)
        self.ex_client = ex_client

    def _on_kline_finished(self):
        if  self.latest_kline_obj:
            close = self.latest_kline_obj.close
            print(f"NoneStrategy: {self.symbol.binance()} {self.timeframe} close: {close}")

