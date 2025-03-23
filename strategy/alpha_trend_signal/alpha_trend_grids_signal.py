import pandas as pd
from pandas import DataFrame

from strategy import Signal
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal

_close = 'close'
_alpha_trend_cross_buy = 'alpha_trend_cross_buy'
_alpha_trend_cross_sell = 'alpha_trend_cross_sell'
_alpha_trend = 'alpha_trend'

_long_trend = 1
_short_trend = -1

class AlphaTrendGridsSignal(Signal):
    """
    高频率(High Frequency)
    适用场景：
    - 小仓位网格开仓条件；
    特点：
    没有多空倾向, 所有信号都是入场信号，所有信号都是出场信号
    """

    def __init__(self, ats: AlphaTrendSignal):
        super().__init__(ats.side)
        self.ats = ats

    def run(self, kline: DataFrame) -> int:
        self.ats.run(kline)
        return 0

    def is_entry(self, df) -> bool:
        return self.ats.is_entry(df) or self.ats.is_exit(df)

    def is_exit(self, df) -> bool:
        return self.ats.is_entry(df) or self.ats.is_exit(df)


