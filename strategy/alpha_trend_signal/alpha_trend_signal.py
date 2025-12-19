import talib as ta
import numpy as np
import pandas as pd
from pandas import DataFrame

from model import OrderSide
from strategy import Signal

_datetime = 'datetime'
_high = 'high'
_low = 'low'
_close = 'close'
_open = 'open'
_volume = 'volume'
_atr = 'atr'
_atr_base_low = 'atr_base_low'
_atr_base_high = 'atr_base_high'
_mfi = 'mfi'
_alpha_trend = 'alpha_trend'
_buy_signal = 'buy_signal'
_sell_signal = 'sell_signal'
_signal = 'signal'


def _alpha_trend_signal(df: DataFrame, atr_multiple: float = 1.0, period: int = 8):
    # 计算技术指标
    high_values, low_values, close_values, volume_values = df[[_high, _low, _close, _volume]].values.astype(np.float64)
    atr_values = ta.ATR(high_values, low_values, close_values, timeperiod=period)
    atr_range_values = atr_values * atr_multiple
    atr_base_low_values = low_values - atr_range_values
    atr_base_high_values = high_values + atr_range_values
    mfi_values = ta.MFI(high_values, low_values, close_values, volume_values, timeperiod=period)

    df[_atr] = atr_values
    df[_atr_base_low] = atr_base_low_values
    df[_atr_base_high] = atr_base_high_values
    df[_mfi] = mfi_values

    alpha_trend_values = np.full(len(df), np.nan)
    if period < len(df):
        alpha_trend_values[period] = atr_base_low_values[period] if mfi_values[period] >= 50 else atr_base_high_values[period]
        
        for i in range(period + 1, len(df)):
            if mfi_values[i] >= 50:
                alpha_trend_values[i] = max(alpha_trend_values[i-1], atr_base_low_values[i])
            else:
                alpha_trend_values[i] = min(alpha_trend_values[i-1], atr_base_high_values[i])
    
    df[_alpha_trend] = alpha_trend_values

    # 通过alpha_trend交叉计算买卖信号
    alpha_trend_shift2 = df[_alpha_trend].shift(2)
    df[_buy_signal] = (df[_alpha_trend] > alpha_trend_shift2).astype('boolean')
    df[_sell_signal] = (df[_alpha_trend] < alpha_trend_shift2).astype('boolean')

    # 新增一列 _signal 用于存储最终信号
    df[_signal] = np.nan
    df[_signal] = np.select(
        [df[_sell_signal], df[_buy_signal]],  # 条件列表，按优先级排序
        [-1, 1],                              # 对应条件的取值
        default=np.nan                        # 默认值
    )

    return df


class AlphaTrendSignal(Signal):
    def __init__(self, side: OrderSide, atr_multiple: float = 1.0, period: int = 8, reverse: bool = False):
        super().__init__(side)
        self.atr_multiple = atr_multiple
        self.period = period
        self.reverse = reverse

        self.datetime: str | None = None
        self.current_signal: int = 0
        self.current_kline_status: int = 0

    def _compute_signal(self, df: DataFrame, first_run: bool = False) -> int:
        df = _alpha_trend_signal(df, self.atr_multiple, self.period)

        if first_run:
            last_valid_index = df[_signal].last_valid_index()
            self.current_signal = int(df[_signal].loc[last_valid_index])

        signal = 0
        last_row = df.iloc[-1]
        if pd.notna(last_row[_buy_signal]) and last_row[_buy_signal]:
            signal = 1
        if pd.notna(last_row[_sell_signal]) and last_row[_sell_signal]:
            signal = -1

        if signal == 0:
            return 0

        if self.current_signal == 0 or self.current_signal != signal:
            self.current_signal = signal
            return signal
        else:
            return 0

    # 真实信号
    def true_signal(self, klines: DataFrame) -> int:
        last_time = klines[_datetime].iloc[-1]

        if self.datetime == last_time:
            return self.current_kline_status
            
        self.current_kline_status = self._compute_signal(klines, self.datetime is None)
        self.datetime = last_time

        return self.current_kline_status

    def run(self, klines: DataFrame) -> int:
        signal = self.true_signal(klines)

        # Apply reversal if enabled
        if self.reverse:
            signal = -signal
        
        return signal
