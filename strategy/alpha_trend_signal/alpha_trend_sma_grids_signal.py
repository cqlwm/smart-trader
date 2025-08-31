import pandas as pd
import talib as ta
from pandas import DataFrame

from strategy import Signal
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal

_sma = 'sma'
_close = 'close'
_alpha_trend = 'alpha_trend'

class AlphaTrendGridsSignal(Signal):
    def __init__(self, ats: AlphaTrendSignal, sma_priod=62):
        super().__init__(ats.side)
        self.ats = ats
        self.sma_priod = sma_priod

    def run(self, kline: DataFrame) -> int:
        self.ats.run(kline)
        kline[_sma] = ta.SMA(kline[_close], timeperiod=self.sma_priod)
        
        # 新增grid_signal列并初始化为0
        kline['grid_signal'] = 0
        
        if len(kline) >= 2:
            prev_row = kline.iloc[-2]
            curr_row = kline.iloc[-1]
            
            # 获取当前alpha_trend和sma值
            alpha_trend_val = curr_row[_alpha_trend]
            sma_val = curr_row[_sma]
            
            # 排除NaN值的无效情况
            if pd.notna(alpha_trend_val) and pd.notna(sma_val) \
                and pd.notna(prev_row[_alpha_trend]) and pd.notna(prev_row[_close]) \
                and pd.notna(curr_row[_close]):
                
                # 检测上穿条件
                cross_up = (prev_row[_close] < prev_row[_alpha_trend]) \
                           and (curr_row[_close] > curr_row[_alpha_trend])
                
                # 检测下穿条件
                cross_down = (prev_row[_close] > prev_row[_alpha_trend]) \
                            and (curr_row[_close] < curr_row[_alpha_trend])
                
                # 判断趋势方向和穿越条件
                if alpha_trend_val < sma_val and cross_up:
                    kline.at[kline.index[-1], 'grid_signal'] = 1
                elif alpha_trend_val > sma_val and cross_down:
                    kline.at[kline.index[-1], 'grid_signal'] = -1
        
        return kline['grid_signal'].iloc[-1]
