import pytest
from datetime import datetime, timezone

from backtest.kline_data_store import KlineDataStore
from model import Symbol, Kline


SYMBOL = Symbol(base='DOGE', quote='USDT')
TIMEFRAME = '5m'
START_DATE = '2026-05-01'
END_DATE = '2026-05-05'


@pytest.fixture(scope="module")
def klines() -> list[Kline]:
    store = KlineDataStore()
    path = store.ensure_data(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        start_time=START_DATE,
        end_time=END_DATE,
        data_dir='data',
    )
    return store.load_csv(path, SYMBOL, TIMEFRAME)


def _ts_to_utc(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


class TestKlineDataTimeRange:
    def test_first_kline_at_start(self, klines: list[Kline]) -> None:
        start_dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
        first_dt = _ts_to_utc(klines[0].timestamp)
        assert first_dt == start_dt

    def test_last_kline_before_end(self, klines: list[Kline]) -> None:
        end_dt = datetime(2026, 5, 5, tzinfo=timezone.utc)
        last_dt = _ts_to_utc(klines[-1].timestamp)
        assert last_dt < end_dt or last_dt == end_dt

    def test_all_klines_within_range(self, klines: list[Kline]) -> None:
        start_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end_ts = int(datetime(2026, 5, 5, tzinfo=timezone.utc).timestamp() * 1000)
        for kline in klines:
            assert start_ts <= kline.timestamp <= end_ts, \
                f"Kline at {_ts_to_utc(kline.timestamp)} outside range"


class TestKlineDataContinuity:
    def test_expected_kline_count(self, klines: list[Kline]) -> None:
        start_dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
        end_dt = datetime(2026, 5, 5, tzinfo=timezone.utc)
        expected_count = int((end_dt - start_dt).total_seconds() / 300) + 1
        assert len(klines) == expected_count, \
            f"Expected {expected_count} klines, got {len(klines)}"

    def test_timestamps_ascending(self, klines: list[Kline]) -> None:
        for i in range(1, len(klines)):
            assert klines[i].timestamp > klines[i - 1].timestamp, \
                f"Timestamp not ascending at index {i}"

    def test_no_duplicate_timestamps(self, klines: list[Kline]) -> None:
        timestamps = [k.timestamp for k in klines]
        assert len(timestamps) == len(set(timestamps))

    def test_five_minute_intervals(self, klines: list[Kline]) -> None:
        interval_ms = 5 * 60 * 1000
        for i in range(1, len(klines)):
            diff = klines[i].timestamp - klines[i - 1].timestamp
            assert diff == interval_ms, \
                f"Gap at index {i}: {diff / 1000}s instead of 300s"


class TestKlineDataOHLCV:
    def test_high_gte_low(self, klines: list[Kline]) -> None:
        for i, kline in enumerate(klines):
            assert kline.high >= kline.low, \
                f"high < low at index {i}: high={kline.high}, low={kline.low}"

    def test_high_gte_open_and_close(self, klines: list[Kline]) -> None:
        for i, kline in enumerate(klines):
            assert kline.high >= kline.open, \
                f"high < open at index {i}: high={kline.high}, open={kline.open}"
            assert kline.high >= kline.close, \
                f"high < close at index {i}: high={kline.high}, close={kline.close}"

    def test_low_lte_open_and_close(self, klines: list[Kline]) -> None:
        for i, kline in enumerate(klines):
            assert kline.low <= kline.open, \
                f"low > open at index {i}: low={kline.low}, open={kline.open}"
            assert kline.low <= kline.close, \
                f"low > close at index {i}: low={kline.low}, close={kline.close}"

    def test_positive_volume(self, klines: list[Kline]) -> None:
        for i, kline in enumerate(klines):
            assert kline.volume > 0, \
                f"Non-positive volume at index {i}: {kline.volume}"

    def test_positive_prices(self, klines: list[Kline]) -> None:
        for i, kline in enumerate(klines):
            assert kline.open > 0, f"Non-positive open at index {i}"
            assert kline.close > 0, f"Non-positive close at index {i}"

    def test_symbol_and_timeframe(self, klines: list[Kline]) -> None:
        for kline in klines:
            assert kline.symbol == SYMBOL
            assert kline.timeframe == TIMEFRAME

    def test_klines_are_finished(self, klines: list[Kline]) -> None:
        for kline in klines:
            assert kline.finished is True
