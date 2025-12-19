from abc import ABC, abstractmethod
import threading
import pandas as pd
from pandas import DataFrame
from typing import Any, List, Dict, Optional

from client.ex_client import ExClient
from model import Kline, OrderSide
import log

logger = log.getLogger(__name__)

class MultiTimeframeStrategy(ABC):
    def __init__(self):
        self.ex_client: ExClient
        # 多时间框架K线数据存储：timeframe -> klines list
        self.klines_dict: Dict[str, List[Dict[str, Any]]] = {}
        # 多时间框架最新K线存储：timeframe -> last kline
        self.last_kline_dict: Dict[str, Optional[Kline]] = {}
        self.init_kline_nums = 300
        self.on_kline_finished_lock = threading.Lock()
        self.on_kline_lock = threading.Lock()
        self.data_lock = threading.Lock()

    def add_timeframe(self, timeframe: str):
        """添加支持的时间框架"""
        if timeframe not in self.klines_dict:
            self.klines_dict[timeframe] = []
            self.last_kline_dict[timeframe] = None

    def klines_to_dataframe(self, timeframe: str) -> DataFrame:
        """将指定时间框架的klines转换为DataFrame进行分析"""
        klines = self.klines_dict.get(timeframe, [])
        if not klines:
            return DataFrame({
                'datetime': pd.Series(dtype='str'),
                'open': pd.Series(dtype='float64'),
                'high': pd.Series(dtype='float64'),
                'low': pd.Series(dtype='float64'),
                'close': pd.Series(dtype='float64'),
                'volume': pd.Series(dtype='float64'),
                'finished': pd.Series(dtype='boolean')
            })

        return DataFrame(klines)

    def get_last_kline(self, timeframe: str) -> Optional[Kline]:
        """获取指定时间框架的最新K线"""
        return self.last_kline_dict.get(timeframe)

    def on_kline(self, timeframe: str):
        """处理K线更新事件（多时间框架版本）"""
        pass

    def on_kline_finished(self, timeframe: str):
        """处理K线完成事件（多时间框架版本）"""
        pass

    def _initialize_klines_if_needed(self, kline: Kline):
        """Initialize klines with historical data if the list is empty"""
        timeframe = kline.timeframe
        if len(self.klines_dict[timeframe]) == 0:
            ohlcv = self.ex_client.fetch_ohlcv(kline.symbol, timeframe, self.init_kline_nums)
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
                self.klines_dict[timeframe].append(historical_kline.to_dict())

    def _update_klines(self, kline: Kline):
        """Update the last kline and manage the klines list"""
        timeframe = kline.timeframe
        self.last_kline_dict[timeframe] = kline

        # 检查是否需要更新最后一个kline或添加新的kline
        if len(self.klines_dict[timeframe]) > 0 and self.klines_dict[timeframe][-1]['datetime'] == kline.datetime:
            self.klines_dict[timeframe][-1] = kline.to_dict()
        else:
            self.klines_dict[timeframe].append(kline.to_dict())

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
        if timeframe not in self.klines_dict:
            self.add_timeframe(timeframe)

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

class StrategyV2(MultiTimeframeStrategy):
    def __init__(self):
        super().__init__()
        # 默认时间框架，用于向后兼容
        self.default_timeframe: str = ""
        # 向后兼容的属性
        self._klines: List[Dict[str, Any]] = []
        self._last_kline: Optional[Kline] = None

    @property
    def klines(self) -> List[Dict[str, Any]]:
        """向后兼容的klines属性，返回默认时间框架的K线数据"""
        if self.default_timeframe:
            return self.klines_dict.get(self.default_timeframe, [])
        return self._klines

    @klines.setter
    def klines(self, value: List[Dict[str, Any]]):
        """向后兼容的klines设置器"""
        if self.default_timeframe:
            self.klines_dict[self.default_timeframe] = value
        else:
            self._klines = value

    @property
    def last_kline(self) -> Optional[Kline]:
        """向后兼容的last_kline属性"""
        if self.default_timeframe:
            return self.last_kline_dict.get(self.default_timeframe)
        return self._last_kline

    @last_kline.setter
    def last_kline(self, value: Optional[Kline]):
        """向后兼容的last_kline设置器"""
        if self.default_timeframe:
            self.last_kline_dict[self.default_timeframe] = value
        else:
            self._last_kline = value

    def klines_to_dataframe(self, timeframe: Optional[str] = None) -> DataFrame:
        """将klines转换为DataFrame进行分析（向后兼容）"""
        if timeframe is None:
            timeframe = self.default_timeframe or ""
        return super().klines_to_dataframe(timeframe)

    def on_kline(self, timeframe: Optional[str] = None):
        """向后兼容的on_kline方法"""
        pass

    def on_kline_finished(self, timeframe: Optional[str] = None):
        """向后兼容的on_kline_finished方法"""
        pass

    def run(self, kline: Kline):
        """处理K线数据，支持单时间框架向后兼容"""
        # 如果还没有设置默认时间框架，则设置为当前K线的时间框架
        if not self.default_timeframe:
            self.default_timeframe = kline.timeframe
            # 将向后兼容的数据迁移到多时间框架结构
            if self._klines:
                self.klines_dict[self.default_timeframe] = self._klines
            if self._last_kline:
                self.last_kline_dict[self.default_timeframe] = self._last_kline

        # 调用父类的多时间框架处理逻辑
        super().run(kline)

        # 为了向后兼容，也调用旧的钩子方法（如果子类没有重写多时间框架版本）
        if hasattr(self, 'on_kline') and callable(getattr(self, 'on_kline')) and len(self.on_kline.__code__.co_varnames) == 1:
            # 如果子类只定义了无参数的on_kline，则调用它
            self._call_on_kline_compat()
        else:
            # 否则调用多时间框架版本
            self._call_on_kline(kline.timeframe)

        if kline.finished:
            if hasattr(self, 'on_kline_finished') and callable(getattr(self, 'on_kline_finished')) and len(self.on_kline_finished.__code__.co_varnames) == 1:
                # 如果子类只定义了无参数的on_kline_finished，则调用它
                self._call_on_kline_finished_compat()
            else:
                # 否则调用多时间框架版本
                self._call_on_kline_finished(kline.timeframe)

    def _call_on_kline_compat(self):
        """Safely call the backward-compatible on_kline method with locking"""
        if self.on_kline_lock.acquire(blocking=False):
            try:
                self.on_kline()
            finally:
                self.on_kline_lock.release()

    def _call_on_kline_finished_compat(self):
        """Safely call the backward-compatible on_kline_finished method with locking"""
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
