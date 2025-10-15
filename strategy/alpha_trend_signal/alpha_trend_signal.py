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

    # 新增一列 _signal 用于存储最终信号
    df[_signal] = np.nan
    df[_signal] = np.select(
        [df[_sell_signal], df[_buy_signal]],  # 条件列表，按优先级排序
        [-1, 1],                              # 对应条件的取值
        default=np.nan                        # 默认值
    )

    # 返回更新后的 DataFrame
    return df


class AlphaTrendSignal(Signal):
    def __init__(self, side: OrderSide, atr_multiple: float = 1.0, period: int = 8):
        super().__init__(side)
        self.atr_multiple = atr_multiple
        self.period = period

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


    def run(self, klines: DataFrame) -> int:
        last_time = klines[_datetime].iloc[-1]
        first_run = self.datetime is None
        if self.datetime == last_time:
            return self.current_kline_status
        else:
            self.datetime = last_time

        self.current_kline_status = self._compute_signal(klines, first_run)
        return self.current_kline_status