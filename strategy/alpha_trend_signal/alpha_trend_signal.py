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
_macd = 'macd'
_macd_signal = 'macd_signal'
_macd_hist = 'macd_hist'

def _alpha_trend_indicator(df: DataFrame, atr_multiple: float = 1.0, period: int = 8,
                        macd_fast_period: int = 12, macd_slow_period: int = 26, macd_signal_period: int = 9):
    # 计算技术指标
    high_values, low_values, close_values, volume_values = df[[_high, _low, _close, _volume]].values.T.astype(np.float64)
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

def _macd_indicator(df: DataFrame, macd_fast_period: int = 12, macd_slow_period: int = 26, macd_signal_period: int = 9) -> DataFrame:
    close_values = np.asarray(df[_close].values, dtype=np.float64)
    macd_values, macd_signal_values, macd_hist_values = ta.MACD(
        close_values,
        fastperiod=macd_fast_period,
        slowperiod=macd_slow_period,
        signalperiod=macd_signal_period
    )

    df[_macd] = macd_values
    df[_macd_signal] = macd_signal_values
    df[_macd_hist] = macd_hist_values
    return df


class AlphaTrendSignal(Signal):
    def __init__(self, side: OrderSide, atr_multiple: float = 1.0, period: int = 8, reverse: bool = False,
                 macd_fast_period: int = 12, macd_slow_period: int = 26, macd_signal_period: int = 9):
        super().__init__(side)
        self.atr_multiple = atr_multiple
        self.period = period
        self.reverse = reverse
        self.macd_fast_period = macd_fast_period
        self.macd_slow_period = macd_slow_period
        self.macd_signal_period = macd_signal_period

        self.datetime: str | None = None
        self.current_signal: int = 0
        self.current_kline_status: int = 0
        self.current_alpha_trend: float = 0.0
        self.current_macd: float = 0.0
        self.current_macd_signal: float = 0.0
        self.current_macd_hist: float = 0.0
        self.previous_macd: float = 0.0
        self.previous_macd_signal: float = 0.0

    def _compute_signal(self, df: DataFrame, first_run: bool = False) -> int:
        
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
    
    def _macd_signal(self, df: DataFrame):
        # Update MACD values for crossover detection
        self.previous_macd = self.current_macd
        self.previous_macd_signal = self.current_macd_signal
        self.current_macd = df[_macd].iloc[-1] if len(df[_macd]) > 0 and pd.notna(df[_macd].iloc[-1]) else 0
        self.current_macd_signal = df[_macd_signal].iloc[-1] if len(df[_macd_signal]) > 0 and pd.notna(df[_macd_signal].iloc[-1]) else 0
        self.current_macd_hist = df[_macd_hist].iloc[-1] if len(df[_macd_hist]) > 0 and pd.notna(df[_macd_hist].iloc[-1]) else 0

    def golden_cross(self) -> bool:
        """Check if MACD line crosses above the signal line (bullish crossover)"""
        return (self.previous_macd < self.previous_macd_signal and
                self.current_macd > self.current_macd_signal)

    def dead_cross(self) -> bool:
        """Check if MACD line crosses below the signal line (bearish crossover)"""
        return (self.previous_macd > self.previous_macd_signal and
                self.current_macd < self.current_macd_signal)

    # 真实信号
    def true_signal(self, klines: DataFrame) -> int:
        last_time = klines[_datetime].iloc[-1]

        if self.datetime == last_time:
            return self.current_kline_status

        df = _alpha_trend_indicator(klines, self.atr_multiple, self.period,
                                self.macd_fast_period, self.macd_slow_period, self.macd_signal_period)        
        self.current_kline_status = self._compute_signal(df, self.datetime is None)
        self.current_alpha_trend = df[_alpha_trend].iloc[-1] if len(df[_alpha_trend]) > 0 else 0

        df = _macd_indicator(df, self.macd_fast_period, self.macd_slow_period, self.macd_signal_period)
        self._macd_signal(df)

        self.datetime = last_time

        return self.current_kline_status

    def run(self, klines: DataFrame) -> int:
        signal = self.true_signal(klines)

        # Apply reversal if enabled
        if self.reverse:
            signal = -signal

        return signal
