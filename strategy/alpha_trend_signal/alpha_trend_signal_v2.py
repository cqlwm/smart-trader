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

class AlphaTrendSignalV2(Signal):
    """
    高频率(High Frequency)
    适用场景：
    - 小仓位网格开仓条件；
    - 持仓盈利时用作分批止盈信号；
    """

    def __init__(self, ats: AlphaTrendSignal, enable_main_trend_check: bool = False):
        super().__init__(ats.side)
        self.ats = ats
        self.enable_main_trend_check = enable_main_trend_check

    def run(self, kline: DataFrame) -> int:
        self.ats.run(kline)

        if len(kline) < 2:
            return 0

        last_row = kline.iloc[-1]
        prev_row = kline.iloc[-2]

        if pd.isna(last_row[_alpha_trend]) or pd.isna(prev_row[_alpha_trend]):
            return 0

        for _trend, trend_cross_condition in [
            (_long_trend, last_row[_close] < last_row[_alpha_trend] < prev_row[_close]),
            (_short_trend, last_row[_close] > last_row[_alpha_trend] > prev_row[_close])
        ]:
            if not self.enable_main_trend_check or self.ats.current_signal == _trend:
                column = _alpha_trend_cross_sell if _trend == _long_trend else _alpha_trend_cross_buy
                kline.at[kline.index[-1], column] = trend_cross_condition
                if trend_cross_condition:
                    return _trend * -1

        return 0
