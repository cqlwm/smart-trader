import pytest
import pandas as pd
from typing import List

from strategy import GeneralStrategy, KlineData
from model import Kline, Symbol
from client.ex_client import ExClient


class MockExClient(ExClient):
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int) -> List[Kline]:
        return []
    
    def balance(self):
        pass
        
    def cancel(self, symbol, order_id):
        pass
        
    def query_order(self, symbol, order_id):
        pass


class DummyStrategy(GeneralStrategy):
    def __init__(self, symbols: List[Symbol], timeframes: List[str]):
        super().__init__(symbols, timeframes)
        self.mock_client = MockExClient()
        # 将最大行数设置得小一点以便于测试
        self.max_kline_nums = 10

    def exchange_client(self) -> ExClient:
        return self.mock_client

    def run(self, kline: Kline):
        super().run(kline)


def test_max_kline_nums_limit():
    symbol = Symbol(base="BTC", quote="USDT")
    timeframe = "1m"
    strategy = DummyStrategy(symbols=[symbol], timeframes=[timeframe])

    # 构造并追加 10 个不同的K线
    for i in range(10):
        # timestamp is ms
        timestamp = 1672531200000 + i * 60000
        kline = Kline(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            open=100.0 + i,
            high=105.0 + i,
            low=95.0 + i,
            close=102.0 + i,
            volume=10.0,
            finished=True
        )
        strategy.run(kline)

    df = strategy.klines(timeframe, symbol)
    # 当追加第 10 个时，len(df) 会达到 10，此时应该触发裁剪，保留后半部分 (10 // 2 = 5) 行
    assert len(df) == 5
    # 验证最后 5 行是否是预期的最后5个
    assert df.iloc[-1]['timestamp'] == 1672531200000 + 9 * 60000
    assert df.iloc[0]['timestamp'] == 1672531200000 + 5 * 60000

    # 再追加一个新行，总行数应该变成 6
    kline11 = Kline(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=1672531200000 + 10 * 60000,
        open=110.0,
        high=115.0,
        low=105.0,
        close=112.0,
        volume=10.0,
        finished=True
    )
    strategy.run(kline11)
    df = strategy.klines(timeframe, symbol)
    assert len(df) == 6
    assert df.iloc[-1]['timestamp'] == 1672531200000 + 10 * 60000

    # 更新同一时间的K线，行数应该保持为 6
    kline11_update = Kline(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=1672531200000 + 10 * 60000,
        open=110.0,
        high=115.0,
        low=105.0,
        close=115.0, # 更新收盘价
        volume=20.0,
        finished=True
    )
    strategy.run(kline11_update)
    df = strategy.klines(timeframe, symbol)
    assert len(df) == 6
    assert df.iloc[-1]['close'] == 115.0
