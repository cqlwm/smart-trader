from abc import ABC, abstractmethod
import threading

import pandas as pd
from pandas import DataFrame
from typing import List, Dict, Optional

from client.ex_client import ExClient
from model import Kline, OrderSide, Symbol
import log
from pydantic import BaseModel

logger = log.getLogger(__name__)

class Strategy(ABC):
    def on_kline(self, timeframe: str, symbol: str):
        pass
    def on_kline_finished(self, timeframe: str, symbol: str):
        pass
    @abstractmethod
    def run(self, kline: Kline):
        pass

class KlineData(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    timeframe: str
    klines: DataFrame
    latest_kline: Kline | None

class GeneralStrategy(Strategy):
    def __init__(self, symbols: List[Symbol], timeframes: List[str]):
        self.ex_client: ExClient
        self.symbols: List[Symbol] = symbols
        self.timeframes: List[str] = timeframes
        # kline_data_dict mappings: symbol -> timeframe -> KlineData
        self.kline_data_dict: Dict[str, Dict[str, KlineData]] = {}
        self.init_kline_nums = 300
        self.on_kline_finished_lock = threading.Lock()
        self.on_kline_lock = threading.Lock()
        self.data_lock = threading.Lock()

        for symbol in symbols:
            sym_binance = symbol.simple()
            self.kline_data_dict[sym_binance] = {}
            for timeframe in timeframes:
                self.kline_data_dict[sym_binance][timeframe] = self._create_empty_kline_data(timeframe)

    @staticmethod
    def _create_empty_kline_data(timeframe: str) -> KlineData:
        empty_df = DataFrame({
            'datetime': pd.Series(dtype='str'),
            'open': pd.Series(dtype='float64'),
            'high': pd.Series(dtype='float64'),
            'low': pd.Series(dtype='float64'),
            'close': pd.Series(dtype='float64'),
            'volume': pd.Series(dtype='float64'),
            'finished': pd.Series(dtype='boolean')
        })
        return KlineData(timeframe=timeframe, klines=empty_df, latest_kline=None)

    def exchange_client(self) -> ExClient:
        raise NotImplementedError()

    def klines(self, timeframe: str, symbol: str) -> DataFrame:
        """将指定时间框架的klines转换为DataFrame进行分析"""
        if symbol not in self.kline_data_dict:
            raise ValueError(f"Symbol {symbol} not found")

        timeframe_dict = self.kline_data_dict[symbol]
        if timeframe not in timeframe_dict:
            raise ValueError(f"Timeframe {timeframe} not found for symbol {symbol}")

        return timeframe_dict[timeframe].klines

    def latest_kline(self, timeframe: str, symbol: str) -> Optional[Kline]:
        """获取指定时间框架的最新K线"""
        if symbol not in self.kline_data_dict:
            raise ValueError(f"Symbol {symbol} not found")

        timeframe_dict = self.kline_data_dict[symbol]
        if timeframe not in timeframe_dict:
            raise ValueError(f"Timeframe {timeframe} not found for symbol {symbol}")

        return timeframe_dict[timeframe].latest_kline

    def on_kline(self, timeframe: str, symbol: str):
        """处理K线更新事件（多时间框架多币种版本）"""
        pass

    def on_kline_finished(self, timeframe: str, symbol: str):
        """处理K线完成事件（多时间框架多币种版本）"""
        pass

    def _initialize_klines_if_needed(self, kline: Kline):
        """Initialize klines with historical data if the DataFrame is empty"""
        timeframe = kline.timeframe
        symbol_binance = kline.symbol.simple()

        if symbol_binance not in self.kline_data_dict:
            self.kline_data_dict[symbol_binance] = {}

        if timeframe not in self.kline_data_dict[symbol_binance]:
            self.kline_data_dict[symbol_binance][timeframe] = self._create_empty_kline_data(timeframe)

        if len(self.kline_data_dict[symbol_binance][timeframe].klines) == 0:
            ohlcv = self.exchange_client().fetch_ohlcv(kline.symbol, timeframe, self.init_kline_nums)
            df = DataFrame([row.to_dict() for row in ohlcv])
            self.kline_data_dict[symbol_binance][timeframe].klines = df

    def _update_klines(self, kline: Kline):
        """Update the last kline and manage the DataFrame"""
        timeframe = kline.timeframe
        symbol_binance = kline.symbol.simple()

        if symbol_binance not in self.kline_data_dict:
            self.kline_data_dict[symbol_binance] = {}

        if timeframe not in self.kline_data_dict[symbol_binance]:
            self.kline_data_dict[symbol_binance][timeframe] = self._create_empty_kline_data(timeframe)

        self.kline_data_dict[symbol_binance][timeframe].latest_kline = kline

        kline_dict = kline.to_dict()
        df = self.kline_data_dict[symbol_binance][timeframe].klines

        # 检查是否需要更新最后一个kline或添加新的kline
        if len(df) > 0 and df['datetime'].iloc[-1] == kline.datetime:
            # Update last row
            df.loc[df.index[-1]] = pd.Series(kline_dict, index=df.columns)
        else:
            # Append new row
            new_df = pd.concat([df, DataFrame([kline_dict])], ignore_index=True)
            self.kline_data_dict[symbol_binance][timeframe].klines = new_df

    def _call_on_kline(self, timeframe: str, symbol: str):
        """Safely call the on_kline method with locking"""
        if self.on_kline_lock.acquire(blocking=False):
            try:
                self.on_kline(timeframe, symbol)
            finally:
                self.on_kline_lock.release()

    def _call_on_kline_finished(self, timeframe: str, symbol: str):
        """Safely call the on_kline_finished method with locking"""
        if self.on_kline_finished_lock.acquire(blocking=False):
            try:
                self.on_kline_finished(timeframe, symbol)
            finally:
                self.on_kline_finished_lock.release()

    def run(self, kline: Kline):
        """处理K线数据（多时间框架多币种版本）"""
        timeframe = kline.timeframe
        symbol_binance = kline.symbol.simple()

        if timeframe not in self.timeframes:
            raise ValueError(f"Timeframe {timeframe} not registered in the strategy")

        if self.data_lock.acquire(blocking=kline.finished):
            try:
                self._initialize_klines_if_needed(kline)
                self._update_klines(kline)
            finally:
                self.data_lock.release()
        else:
            return

        self._call_on_kline(timeframe, symbol_binance)

        if kline.finished:
            self._call_on_kline_finished(timeframe, symbol_binance)

class SimpleStrategy(GeneralStrategy):
    def __init__(self, symbol: Symbol, timeframe: str):
        super().__init__(symbols=[symbol], timeframes=[timeframe])

    @property
    def timeframe(self) -> str:
        """Get the single timeframe"""
        return self.timeframes[0]

    @property
    def symbol(self):
        """Get the symbol"""
        return self.symbols[0]

    @property
    def klines_df(self) -> DataFrame:
        """Get the klines DataFrame for the single timeframe and single symbol"""
        return self.klines(self.timeframe, self.symbol.simple())

    @property
    def latest_kline_obj(self) -> Kline | None:
        """Get the latest Kline for the single timeframe and single symbol"""
        return self.latest_kline(self.timeframe, self.symbol.simple())

    def _on_kline(self):
        pass

    def _on_kline_finished(self):
        pass

    def on_kline(self, timeframe: str, symbol: str):
        if timeframe == self.timeframe and symbol == self.symbol.simple():
            self._on_kline()

    def on_kline_finished(self, timeframe: str, symbol: str):
        if timeframe == self.timeframe and symbol == self.symbol.simple():
            self._on_kline_finished()

class Signal:
    def __init__(self, side: OrderSide):
        self.side: OrderSide = side

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
