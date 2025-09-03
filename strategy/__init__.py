from abc import ABC, abstractmethod
from datetime import datetime
import pandas as pd
from pandas import DataFrame
import builtins
from enum import Enum
from dataclasses import dataclass

from client.ex_client import ExClient
from model import Kline, OrderSide


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

    def exit_id(self, i: int = None):
        exit_id = self.custom_id.replace(self.side.value, self.side.reversal().value, 1)
        return exit_id if i is None else f'{exit_id}{i}'

class StrategyV2(ABC):
    def __init__(self):
        self.ex_client: ExClient
        self.klines: DataFrame = DataFrame(columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        self.last_kline: Kline

    def on_kline(self):
        pass

    def on_kline_finished(self):
        pass

    def run(self, kline: Kline):
        if len(self.klines) == 0:
            ohlcv = self.ex_client.fetch_ohlcv(kline.symbol, kline.timeframe, 300)
            df = pd.DataFrame(ohlcv, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = df['datetime'].apply(lambda x: datetime.fromtimestamp(x / 1000).strftime('%Y-%m-%d %H:%M:%S'))
            self.klines = df
        
        self.last_kline = kline
        self.on_kline()

        if self.last_kline.finished:
            if len(self.klines) > 0 and self.klines['datetime'].iloc[-1] == self.last_kline.datetime:
                kline_dict = self.last_kline.to_dict()
                self.klines.iloc[-1] = pd.Series(kline_dict, index=self.klines.columns)
            else:
                new_series = pd.Series(self.last_kline.to_dict(), index=self.klines.columns)
                self.klines.loc[len(self.klines)] = new_series
            self.on_kline_finished()

class Strategy(ABC):
    def __init__(self):
        self.df = None

    @abstractmethod
    def run(self, kline: DataFrame):
        pass


class Signal(Strategy):
    def __init__(self, side: str):
        super().__init__()
        if side not in ['buy','sell']:
            raise Exception('side must be buy or sell')
        self.side = side

    @abstractmethod
    def run(self, kline: DataFrame) -> int:
        pass

    def is_entry(self, df) -> bool:
        signal = self.run(df)
        if self.side == 'buy':
            return signal == 1
        elif self.side == 'sell':
            return signal == -1
        return False

    def is_exit(self, df) -> bool:
        signal = self.run(df)
        if self.side == 'buy':
            return signal == -1
        elif self.side == 'sell':
            return signal == 1
        return False