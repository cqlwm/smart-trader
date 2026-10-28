"""行情图表服务测试: K线 + 指标对齐 / 空行情 / 非法周期"""

import pytest

from smart_trader.api.market_service import build_market_chart
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import Kline
from tests.fakes import DEFAULT_SYMBOL, FakeMarketDataPort


def _klines(count: int = 80) -> list[Kline]:
    result: list[Kline] = []
    for index in range(count):
        close = 1.0 + index * 0.01
        result.append(
            Kline(
                symbol=DEFAULT_SYMBOL,
                timeframe="1m",
                open=close - 0.005,
                high=close + 0.01,
                low=close - 0.01,
                close=close,
                volume=100.0 + index,
                timestamp=1_700_000_000_000 + index * 60_000,
                finished=True,
            )
        )
    return result


def test_chart_returns_candles_and_aligned_indicator():
    klines = _klines()
    market = FakeMarketDataPort(klines=klines)

    chart = build_market_chart(market, DEFAULT_SYMBOL, "1m", 80)

    assert len(chart.candles) == 80
    assert len(chart.indicator) == 80
    assert chart.candles[0].timestamp == klines[0].timestamp
    assert chart.candles[-1].close == pytest.approx(klines[-1].close)
    assert chart.last_price == pytest.approx(klines[-1].close)
    assert chart.indicator[-1].timestamp == klines[-1].timestamp
    assert chart.indicator[-1].trend == chart.trend
    assert chart.direction in {"long", "short", "neutral"}
    if chart.last_breakout_timestamp is not None:
        assert chart.last_breakout_timestamp in {
            candle.timestamp for candle in chart.candles
        }


def test_empty_market_raises_market_data_unavailable():
    market = FakeMarketDataPort(klines=[])

    with pytest.raises(SmartTraderError) as error:
        build_market_chart(market, DEFAULT_SYMBOL, "1m", 80)

    assert error.value.error_code is ErrorCode.MARKET_DATA_UNAVAILABLE


def test_invalid_timeframe_raises_value_error():
    market = FakeMarketDataPort(klines=_klines())

    with pytest.raises(ValueError):
        build_market_chart(market, DEFAULT_SYMBOL, "1x", 80)
