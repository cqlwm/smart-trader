import talib as ta
import numpy as np
import pandas as pd
from pandas import DataFrame

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


def _alpha_trend_signal(df, atr_multiple=1.0, period=8):
    # 计算技术指标
    df[_atr] = ta.ATR(df[_high], df[_low], df[_close], timeperiod=period)
    df[_atr_base_low] = df[_low] - df[_atr] * atr_multiple
    df[_atr_base_high] = df[_high] + df[_atr] * atr_multiple
    df[_mfi] = ta.MFI(df[_high], df[_low], df[_close], df[_volume], timeperiod=period)

    # 初始化 _alpha_trend 列
    df[_alpha_trend] = np.nan
    
    # 预提取列数据到 NumPy 数组，避免在循环中使用 df.at
    mfi_values = df[_mfi].values
    atr_base_low_values = df[_atr_base_low].values
    atr_base_high_values = df[_atr_base_high].values
    
    # 创建 alpha_trend 数组并初始化第一个有效值
    alpha_trend_values = np.full(len(df), np.nan)
    
    # 设置初始值
    if period < len(df):
        # 只在必要时使用一次 df.at
        alpha_trend_values[period] = atr_base_low_values[period] if mfi_values[period] >= 50 else atr_base_high_values[period]
        
        # 使用 NumPy 数组进行循环计算，避免 df.at 调用
        for i in range(period + 1, len(df)):
            if mfi_values[i] >= 50:
                alpha_trend_values[i] = max(alpha_trend_values[i-1], atr_base_low_values[i])
            else:
                alpha_trend_values[i] = min(alpha_trend_values[i-1], atr_base_high_values[i])
    
    # 将计算结果赋值回 DataFrame
    df[_alpha_trend] = alpha_trend_values

    # 计算买卖信号
    alpha_trend_shift2 = df[_alpha_trend].shift(2)
    df[_buy_signal] = (df[_alpha_trend] > alpha_trend_shift2).astype('boolean')
    df[_sell_signal] = (df[_alpha_trend] < alpha_trend_shift2).astype('boolean')

    # 返回更新后的 DataFrame
    return df


class AlphaTrendSignal(Signal):
    def __init__(self, side, atr_multiple=1.0, period=8):
        super().__init__(side)
        self.atr_multiple = atr_multiple
        self.period = period

        self.datetime = None
        self.current_signal = 0
        self.current_kline_status = 0

    def _compute_signal(self, df):
        df = _alpha_trend_signal(df, self.atr_multiple, self.period)

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


    def run(self, klines: DataFrame) -> int:
        last_time = klines[_datetime].iloc[-1]
        if self.datetime == last_time:
            return self.current_kline_status
        else:
            self.datetime = last_time

        self.current_kline_status = self._compute_signal(klines)
        return self.current_kline_status