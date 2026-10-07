import pandas as pd

from smart_trader.model import Kline, Symbol
from smart_trader.strategy import MultiTimeframeStrategy, SingleTimeframeStrategy
from tests.fakes import FakeMarketDataPort

SYMBOL = Symbol(base="DOGE", quote="USDT")


class RecordingStrategy(SingleTimeframeStrategy):
    def __init__(self, market: FakeMarketDataPort) -> None:
        super().__init__("1m", market)
        self.callbacks: list[str] = []

    def _on_kline(self) -> None:
        self.callbacks.append("on_kline")

    def _on_kline_finished(self) -> None:
        self.callbacks.append("on_kline_finished")


def build_kline(finished: bool = True, close: float = 1.5) -> Kline:
    return Kline(
        symbol=SYMBOL,
        timeframe="1m",
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=100.0,
        timestamp=1_700_000_000_000,
        finished=finished,
    )


def test_initializes_history_from_market_data_port():
    market = FakeMarketDataPort()
    market.set_klines([build_kline()])
    strategy = RecordingStrategy(market)

    strategy.run(build_kline())

    assert len(strategy.klines_df) == 1, "首次运行应拉取历史K线"
    assert strategy.latest_kline_obj is not None


def test_does_not_fetch_history_twice():
    market = FakeMarketDataPort()
    market.set_klines([build_kline()])
    strategy = RecordingStrategy(market)
    strategy.run(build_kline())

    strategy.run(build_kline(finished=False, close=1.6))

    assert market.ohlcv_calls == 1, "历史数据只初始化一次"


def test_calls_both_callbacks_for_finished_kline():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)

    strategy.run(build_kline(finished=True))

    assert strategy.callbacks == ["on_kline", "on_kline_finished"]


def test_calls_only_on_kline_for_unfinished_kline():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)

    strategy.run(build_kline(finished=False))

    assert strategy.callbacks == ["on_kline"]


def test_rejects_unregistered_timeframe():
    market = FakeMarketDataPort()
    strategy = RecordingStrategy(market)
    kline = build_kline()
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
