from model import Symbol
from strategy import SingleTimeframeStrategy


class NoneStrategy(SingleTimeframeStrategy):
    """空策略，不做任何操作"""
    def __init__(self, symbol: Symbol, timeframe: str):
        super().__init__(timeframe)
        self.symbol = symbol

    
    def _on_kline_finished(self):
        if  self.latest_kline_obj:
            close = self.latest_kline_obj.close
            print(f"NoneStrategy: {self.symbol.binance()} {self.timeframe} close: {close}")

