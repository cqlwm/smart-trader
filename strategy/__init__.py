from abc import ABC, abstractmethod
import threading
import pandas as pd
from pandas import DataFrame
from typing import List, Dict

from client.ex_client import ExClient
from model import Kline, OrderSide
import log

logger = log.getLogger(__name__)

class StrategyV2(ABC):
    def __init__(self):
        self.ex_client: ExClient
        self.klines: List[Dict] = []
        self.last_kline: Kline
        self.init_kline_nums = 300
        self.on_kline_finished_lock = threading.Lock()
        self.on_kline_lock = threading.Lock()
    
    def klines_to_dataframe(self) -> DataFrame:
        """将klines转换为DataFrame进行分析"""
        if not self.klines:
            return DataFrame({
                'datetime': pd.Series(dtype='str'),
                'open': pd.Series(dtype='float64'),
                'high': pd.Series(dtype='float64'),
                'low': pd.Series(dtype='float64'),
                'close': pd.Series(dtype='float64'),
                'volume': pd.Series(dtype='float64'),
                'finished': pd.Series(dtype='boolean')
            })
        
        return DataFrame(self.klines)

    def on_kline(self):
        pass

    def on_kline_finished(self):
        pass

    def run(self, kline: Kline):
        if len(self.klines) == 0:
            ohlcv = self.ex_client.fetch_ohlcv(kline.symbol, kline.timeframe, self.init_kline_nums)
            for row in ohlcv:
                timestamp, open_price, high_price, low_price, close_price, volume = row
                historical_kline = Kline(
                    symbol=kline.symbol,
                    timeframe=kline.timeframe,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    timestamp=timestamp,
                    finished=True
                )
                self.klines.append(historical_kline.to_dict())
        
        self.last_kline = kline

        if self.on_kline_lock.acquire(blocking=False):
            try:
                self.on_kline()
            finally:
                self.on_kline_lock.release()

        if self.last_kline.finished:
            # 检查是否需要更新最后一个kline或添加新的kline
            if len(self.klines) > 0 and self.klines[-1]['datetime'] == self.last_kline.datetime:
                self.klines[-1] = self.last_kline.to_dict()
            else:
                self.klines.append(self.last_kline.to_dict())
            
            if self.on_kline_finished_lock.acquire(blocking=False):
                try:
                    self.on_kline_finished()
                finally:
                    self.on_kline_finished_lock.release()


class Signal:
    def __init__(self, side: OrderSide):
        self.side: OrderSide = side

    @abstractmethod
    def run(self, klines: DataFrame) -> int:
        pass

    def is_entry(self, df: DataFrame) -> bool:
        signal = self.run(df)
        if self.side == OrderSide.BUY:
            return signal == 1
        elif self.side == OrderSide.SELL:
            return signal == -1
        return False

    def is_exit(self, df: DataFrame) -> bool:
        signal = self.run(df)
        if self.side == OrderSide.BUY:
            return signal == -1
        elif self.side == OrderSide.SELL:
            return signal == 1
        return False