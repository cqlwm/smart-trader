import talib as ta
import numpy as np

from strategy import Signal

_timestamp = 'datetime'
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
_last_calculated = 'last_calculated'


def _alpha_trend_signal(df, atr_multiple=1.0, period=8):
    # 检查是否有上次计算的位置记录
    if _last_calculated not in df.columns:
        df[_last_calculated] = -1  # 初始化为 -1，表示从头开始计算

    last_calculated = int(df[_last_calculated].max())
    if last_calculated > len(df):
        # 说明发生了裁剪，重置 _last_calculated
        last_calculated = -1

    df[_atr] = ta.ATR(df[_high], df[_low], df[_close], timeperiod=period)
    df[_atr_base_low] = df[_low] - df[_atr] * atr_multiple
    df[_atr_base_high] = df[_high] + df[_atr] * atr_multiple
    df[_mfi] = ta.MFI(df[_high], df[_low], df[_close], df[_volume], timeperiod=period)

    if last_calculated == -1:
        # 初始化 _alpha_trend 列
        df[_alpha_trend] = np.nan
        df.at[period, _alpha_trend] = df.at[period, _atr_base_low] if df.at[period, _mfi] >= 50 else df.at[
            period, _atr_base_high]
        last_calculated = period

    # 计算新的 _alpha_trend 值
    for i in range(last_calculated + 1, len(df)):
        prev_alpha_trend = df.at[i - 1, _alpha_trend]
        if df.at[i, _mfi] >= 50:
            df.at[i, _alpha_trend] = prev_alpha_trend if df.at[i, _atr_base_low] < prev_alpha_trend else df.at[
                i, _atr_base_low]
        else:
            df.at[i, _alpha_trend] = prev_alpha_trend if df.at[i, _atr_base_high] > prev_alpha_trend else df.at[
                i, _atr_base_high]

    # 计算买卖信号
    df[_buy_signal] = df[_alpha_trend] > df[_alpha_trend].shift(2)
    df[_sell_signal] = df[_alpha_trend] < df[_alpha_trend].shift(2)

    # 更新上次计算的位置
    df[_last_calculated] = len(df) - 1

    # 返回更新后的 DataFrame
    return df


class AlphaTrendSignal(Signal):
    def __init__(self, side, atr_multiple=1.0, period=8, additional_signal=None):
        super().__init__(side)
        self.df = None
        self.atr_multiple = atr_multiple
        self.period = period

        self.timestamp = None
        self.current_signal = 0
        self.kline_status = 0

        if additional_signal is not None:
            self.additional_signal = additional_signal
        else:
            self.additional_signal = self

    def _compute_signal(self, df):
        if self.df is None:
            self.df = df
            self.recover_last_signal()
        else:
            _alpha_trend_signal(df, self.atr_multiple, self.period)

        signal = 0
        if df[_buy_signal].iloc[-1]:
            signal = 1
        if df[_sell_signal].iloc[-1]:
            signal = -1

        if signal == 0:
            return 0

        if self.current_signal == 0 or self.current_signal != signal:
            self.current_signal = signal
            additional_signal = self.additional_signal.current_signal
            if additional_signal == signal or additional_signal == 0:
                return signal
            else:
                return 0
        else:
            return 0


    def run(self, df):
        last_time = df[_timestamp].iloc[-1]
        if self.timestamp == last_time:
            return self.kline_status
        else:
            self.timestamp = last_time

        self.kline_status = self._compute_signal(df)
        return self.kline_status


    def recover_last_signal(self):
        _alpha_trend_signal(self.df, self.atr_multiple, self.period)
        if self.current_signal == 0:
            df = self.df
            last_index_bs = df[df[_buy_signal]].last_valid_index()
            last_index_ss = df[df[_sell_signal]].last_valid_index()
            if last_index_bs is None and last_index_ss is None:
                return
            elif last_index_bs is None:
                self.current_signal = -1
            elif last_index_ss is None:
                self.current_signal = 1
            else:
                self.current_signal = 1 if last_index_bs > last_index_ss else -1
