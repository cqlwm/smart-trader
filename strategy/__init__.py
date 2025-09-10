from abc import ABC, abstractmethod
import threading
import pandas as pd
from pandas import DataFrame
import builtins
from dataclasses import dataclass
from typing import List, Dict

from client.ex_client import ExClient
from model import Kline, OrderSide
import log

logger = log.getLogger(__name__)

@dataclass
class Order:
    # ID规则 side + 10random + [i]
    custom_id: str
    side: OrderSide
    price: float
    quantity: float
    take_profit_rate: float
    min_profit_rate: float = 0.002

    def total_value(self):
        return self.price * self.quantity

    # profit_level：表示盈利级别，值为 0损失手续费，-1不可盈利，1可盈利，2达到止盈标准
    def profit_level(self, current_price) -> int:
        compare_fun = builtins.float.__gt__
        if self.side == OrderSide.SELL:
            compare_fun = builtins.float.__lt__

        if compare_fun(current_price, self.take_profit_price()):
            return 2
        elif compare_fun(current_price, self.breakeven_price()):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    def loss_rate(self, current_price):
        if self.profit_level(current_price) < 0:
            return float("{:.4f}".format(abs(current_price - self.price) / self.price))
        else:
            return 0

    def _profit(self, rate):
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)

    def take_profit_price(self):
        return self._profit(self.take_profit_rate)

    def breakeven_price(self):
        return self._profit(self.min_profit_rate)

    def exit_id(self, i: int | None = None):
        exit_id = self.custom_id.replace(self.side.value, self.side.reversal().value, 1)
        return exit_id if i is None else f'{exit_id}{i}'

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