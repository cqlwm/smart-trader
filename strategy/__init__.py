from abc import ABC, abstractmethod
import threading
import pandas as pd
from pandas import DataFrame
from typing import Any, List, Dict, Optional

from client.ex_client import ExClient
from model import Kline, OrderSide
import log
from pydantic import BaseModel

logger = log.getLogger(__name__)

class Strategy(ABC):
    def on_kline(self, timeframe: str):
        pass
    def on_kline_finished(self, timeframe: str):
        pass
    @abstractmethod
    def run(self, kline: Kline):
        pass

class KlineData(BaseModel):
    timeframe: str
    klines: DataFrame
    latest_kline: Optional[Kline]

class MultiTimeframeStrategy(Strategy):
    def __init__(self, timeframes: List[str]):
        self.ex_client: ExClient
        self.timeframes: List[str] = timeframes
        self.kline_data_dict: Dict[str, KlineData] = {}
        self.init_kline_nums = 300
        self.on_kline_finished_lock = threading.Lock()
        self.on_kline_lock = threading.Lock()
        self.data_lock = threading.Lock()

        for timeframe in timeframes:
            empty_df = DataFrame({
                'datetime': pd.Series(dtype='str'),
                'open': pd.Series(dtype='float64'),
                'high': pd.Series(dtype='float64'),
                'low': pd.Series(dtype='float64'),
                'close': pd.Series(dtype='float64'),
                'volume': pd.Series(dtype='float64'),
                'finished': pd.Series(dtype='boolean')
            })
            self.kline_data_dict[timeframe] = KlineData(timeframe=timeframe, klines=empty_df, latest_kline=None)

    def klines(self, timeframe: str) -> DataFrame:
        """将指定时间框架的klines转换为DataFrame进行分析"""
        if timeframe not in self.kline_data_dict:
            raise ValueError(f"Timeframe {timeframe} not found")
        return self.kline_data_dict[timeframe].klines

    def latest_kline(self, timeframe: str) -> Optional[Kline]:
        """获取指定时间框架的最新K线"""
        if timeframe not in self.kline_data_dict:
            raise ValueError(f"Timeframe {timeframe} not found")
        return self.kline_data_dict[timeframe].latest_kline

    def on_kline(self, timeframe: str):
        """处理K线更新事件（多时间框架版本）"""
        pass

    def on_kline_finished(self, timeframe: str):
        """处理K线完成事件（多时间框架版本）"""
        pass

    def _initialize_klines_if_needed(self, kline: Kline):
        """Initialize klines with historical data if the DataFrame is empty"""
        timeframe = kline.timeframe
        if len(self.kline_data_dict[timeframe].klines) == 0:
            ohlcv = self.ex_client.fetch_ohlcv(kline.symbol, timeframe, self.init_kline_nums)
            rows = []
            for row in ohlcv:
                timestamp, open_price, high_price, low_price, close_price, volume = row
                historical_kline = Kline(
                    symbol=kline.symbol,
                    timeframe=timeframe,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    timestamp=timestamp,
                    finished=True
                )
                rows.append(historical_kline.to_dict())
            df = DataFrame(rows)
            self.kline_data_dict[timeframe].klines = df

    def _update_klines(self, kline: Kline):
        """Update the last kline and manage the DataFrame"""
        timeframe = kline.timeframe
        self.kline_data_dict[timeframe].latest_kline = kline

        kline_dict = kline.to_dict()
        df = self.kline_data_dict[timeframe].klines

        # 检查是否需要更新最后一个kline或添加新的kline
        if len(df) > 0 and df.iloc[-1]['datetime'] == kline.datetime:
            # Update last row
            df.loc[df.index[-1]] = kline_dict
        else:
            # Append new row
            new_df = pd.concat([df, DataFrame([kline_dict])], ignore_index=True)
            self.kline_data_dict[timeframe].klines = new_df

    def _call_on_kline(self, timeframe: str):
        """Safely call the on_kline method with locking"""
        if self.on_kline_lock.acquire(blocking=False):
            try:
                self.on_kline(timeframe)
            finally:
                self.on_kline_lock.release()

    def _call_on_kline_finished(self, timeframe: str):
        """Safely call the on_kline_finished method with locking"""
        if self.on_kline_finished_lock.acquire(blocking=False):
            try:
                self.on_kline_finished(timeframe)
            finally:
                self.on_kline_finished_lock.release()

    def run(self, kline: Kline):
        """处理K线数据（多时间框架版本）"""
        timeframe = kline.timeframe
        # 确保时间框架已注册
        if timeframe not in self.kline_data_dict:
            raise ValueError(f"Timeframe {timeframe} not registered in the strategy")

        if self.data_lock.acquire(blocking=kline.finished):
            try:
                self._initialize_klines_if_needed(kline)
                self._update_klines(kline)
            finally:
                self.data_lock.release()
        else:
            return

        self._call_on_kline(timeframe)

        if kline.finished:
            self._call_on_kline_finished(timeframe)

class SingleTimeframeStrategy(MultiTimeframeStrategy):
    def __init__(self, timeframe: str):
        super().__init__([timeframe])

    @property
    def timeframe(self) -> str:
        """Get the single timeframe"""
        return self.timeframes[0]

    @property
    def klines_df(self) -> DataFrame:
        """Get the klines DataFrame for the single timeframe"""
        return self.klines(self.timeframe)

    @property
    def latest_kline_obj(self) -> Optional[Kline]:
        """Get the latest Kline for the single timeframe"""
        return self.latest_kline(self.timeframe)
    
    def _on_kline(self):
        pass
    
    def _on_kline_finished(self):
        pass

    def on_kline(self, timeframe: str):
        self._on_kline()

    def on_kline_finished(self, timeframe: str):
        self._on_kline_finished()


class StrategyV2(ABC):
    def __init__(self):
        self.ex_client: ExClient
        self.klines: List[Dict[str, Any]] = []
        self.last_kline: Kline
        self.init_kline_nums = 300
        self.on_kline_finished_lock = threading.Lock()
        self.on_kline_lock = threading.Lock()
        self.data_lock = threading.Lock()
    
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

    def _initialize_klines_if_needed(self, kline: Kline):
        """Initialize klines with historical data if the list is empty"""
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

    def _update_klines(self, kline: Kline):
        """Update the last kline and manage the klines list"""
        self.last_kline = kline

        # 检查是否需要更新最后一个kline或添加新的kline
        if len(self.klines) > 0 and self.klines[-1]['datetime'] == kline.datetime:
            self.klines[-1] = kline.to_dict()
        else:
            self.klines.append(kline.to_dict())

    def _call_on_kline(self):
        """Safely call the on_kline method with locking"""
        if self.on_kline_lock.acquire(blocking=False):
            try:
                self.on_kline()
            finally:
                self.on_kline_lock.release()

    def _call_on_kline_finished(self):
        """Safely call the on_kline_finished method with locking"""
        if self.on_kline_finished_lock.acquire(blocking=False):
            try:
                self.on_kline_finished()
            finally:
                self.on_kline_finished_lock.release()

    def run(self, kline: Kline):
        if self.data_lock.acquire(blocking=kline.finished):
            try:
                self._initialize_klines_if_needed(kline)
                self._update_klines(kline)
            finally:
                self.data_lock.release()
        else:
            return

        self._call_on_kline()

        if kline.finished:
            self._call_on_kline_finished()


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
