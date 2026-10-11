import pandas as pd

from smart_trader.model import Symbol
from smart_trader.strategy import MultiTimeframeStrategy, SingleTimeframeStrategy
from tests.fakes import FakeMarketDataPort, make_kline

SYMBOL = Symbol(base="DOGE", quote="USDT")


class RecordingStrategy(SingleTimeframeStrategy):
    def __init__(self, market: FakeMarketDataPort) -> None:
        super().__init__("1m", market)
        self.callbacks: list[str] = []

    def _on_kline(self) -> None:
        self.callbacks.append("on_kline")

    def _on_kline_finished(self) -> None:
        self.callbacks.append("on_kline_finished")


def test_initializes_history_from_market_data_port():
    market = FakeMarketDataPort()
    market.set_klines([make_kline()])
    strategy = RecordingStrategy(market)

    strategy.run(make_kline())

    assert len(strategy.klines_df) == 1, "首次运行应拉取历史K线"
    assert strategy.latest_kline_obj is not None


def test_does_not_fetch_history_twice():
    market = FakeMarketDataPort()
    market.set_klines([make_kline()])
    strategy = RecordingStrategy(market)
    strategy.run(make_kline())

    strategy.run(make_kline(finished=False, close=1.6))

    assert market.ohlcv_calls == 1, "历史数据只初始化一次"


def test_calls_both_callbacks_for_finished_kline():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)

    strategy.run(make_kline())

    assert strategy.callbacks == ["on_kline", "on_kline_finished"]


def test_calls_only_on_kline_for_unfinished_kline():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)

    strategy.run(make_kline(finished=False))

    assert strategy.callbacks == ["on_kline"]


def test_rejects_unregistered_timeframe():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)
    kline = make_kline()
    kline.timeframe = "5m"

    try:
        strategy.run(kline)
    except ValueError as e:
        assert "5m" in str(e)
    else:
        raise AssertionError("未注册的周期必须抛出 ValueError")


def test_multi_timeframe_independent_dataframes():
    market = FakeMarketDataPort()
    strategy = MultiTimeframeStrategy(["1m", "5m"], market)

    assert set(strategy.kline_data_dict) == {"1m", "5m"}
    assert isinstance(strategy.klines("1m"), pd.DataFrame)
    assert strategy.klines("1m") is not strategy.klines("5m")
