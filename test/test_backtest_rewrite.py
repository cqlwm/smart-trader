import pytest
from unittest.mock import patch, MagicMock
from typing import Any

from model import Symbol, Kline
from backtest.config import BacktestConfig
from backtest.result import BacktestResult

from backtest.backtest_client import BacktestClient
from backtest.analyzer import BacktestAnalyzer
from backtest.kline_data_store import KlineDataStore
from persistence.order_repository import InMemoryOrderRepository


SYMBOL = Symbol(base='ETH', quote='USDT')
TS_BASE = 1_700_000_000_000


def _make_klines(count: int) -> list[Kline]:
    return [
        Kline(
            symbol=SYMBOL,
            timeframe='1m',
            open=2000.0 + i,
            high=2010.0 + i,
            low=1990.0 + i,
            close=2005.0 + i,
            volume=100.0,
            timestamp=TS_BASE + i * 60_000,
            finished=True,
        )
        for i in range(count)
    ]


def _client_with_data(kline_count: int = 10) -> BacktestClient:
    """Create a BacktestClient with pre-loaded kline data for testing."""
    client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
    klines = _make_klines(kline_count)
    client._store_klines(SYMBOL, '1m', klines)
    return client


class TestBacktestConfig:
    def test_frozen_dataclass(self) -> None:
        config = BacktestConfig(
            config_path="strategies.yaml",
            symbol=SYMBOL,
            timeframe="1m",
            start_date="2025-01-01",
            end_date="2025-06-01",
        )
        assert config.config_path == "strategies.yaml"
        assert config.initial_balance == 10000.0
        assert config.maker_fee == 0.0002
        assert config.taker_fee == 0.0004
        assert config.start_index == 300

        with pytest.raises(AttributeError):
            config.initial_balance = 999  # type: ignore[misc]

    def test_custom_fees_and_start_index(self) -> None:
        config = BacktestConfig(
            config_path="custom.yaml",
            symbol=SYMBOL,
            timeframe="5m",
            start_date="2025-01-01",
            end_date="2025-02-01",
            initial_balance=50000.0,
            maker_fee=0.001,
            taker_fee=0.002,
            start_index=0,
            data_dir="my_data",
        )
        assert config.initial_balance == 50000.0
        assert config.maker_fee == 0.001
        assert config.taker_fee == 0.002
        assert config.start_index == 0
        assert config.data_dir == "my_data"
        assert config.config_path == "custom.yaml"

    def test_default_config_path(self) -> None:
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe="1m",
            start_date="2025-01-01",
            end_date="2025-06-01",
        )
        assert config.config_path == "strategies.yaml"


class TestBacktestResult:
    def test_frozen_dataclass(self) -> None:
        result = BacktestResult(
            analysis={"summary": {"total_trades": 5}},
            trade_history=[{"id": "1"}],
            final_balance=10500.0,
            report="test report",
        )
        assert result.final_balance == 10500.0
        assert result.report == "test report"
        assert len(result.trade_history) == 1

        with pytest.raises(AttributeError):
            result.final_balance = 0  # type: ignore[misc]


class TestBacktestClientCleanup:
    def test_no_threading_lock(self) -> None:
        """Verify threading.RLock was removed from BacktestClient."""
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10000.0)
        assert not hasattr(client, 'lock')

    def test_update_current_price_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        client.update_current_price(SYMBOL, 2000.0)
        assert client.get_current_price(SYMBOL) == 2000.0

    def test_update_current_timestamp_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        client.update_current_timestamp(TS_BASE)
        assert client.current_timestamp == TS_BASE

    def test_store_klines_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(5)
        client._store_klines(SYMBOL, '1m', klines)
        assert len(client._kline_cache[f"{SYMBOL.binance()}_1m"]) == 5

    def test_fetch_ohlcv_no_lock(self) -> None:
        client = _client_with_data(10)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)
        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3)
        assert len(result) == 3
        assert result[-1].timestamp == TS_BASE + 5 * 60_000


class TestBacktestAnalyzer:
    def test_empty_trade_history(self) -> None:
        analyzer = BacktestAnalyzer(initial_balance=10000.0)
        analysis = analyzer.analyze([])
        assert analysis['summary']['total_trades'] == 0
        assert analysis['summary']['total_return'] == 0.0

    def test_generate_report(self) -> None:
        analyzer = BacktestAnalyzer(initial_balance=10000.0)
        analysis = analyzer.analyze([])
        report = analyzer.generate_report(analysis)
        assert "BACKTEST REPORT" in report
        assert "SUMMARY" in report
        assert "RISK METRICS" in report
        assert "TRADE METRICS" in report


class TestFetchOhlcvTimeParams:
    def test_fetch_ohlcv_with_start_time(self) -> None:
        client = _client_with_data(10)

        start_ts = TS_BASE + 3 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', start_time=start_ts)
        assert len(result) == 7
        assert result[0].timestamp == start_ts

    def test_fetch_ohlcv_with_end_time(self) -> None:
        client = _client_with_data(10)

        end_ts = TS_BASE + 5 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', end_time=end_ts)
        assert len(result) == 6
        assert result[-1].timestamp == end_ts

    def test_fetch_ohlcv_with_time_range(self) -> None:
        client = _client_with_data(10)

        start_ts = TS_BASE + 2 * 60_000
        end_ts = TS_BASE + 7 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', start_time=start_ts, end_time=end_ts)
        assert len(result) == 6
        assert result[0].timestamp == start_ts
        assert result[-1].timestamp == end_ts

    def test_fetch_ohlcv_time_range_with_limit(self) -> None:
        client = _client_with_data(10)

        start_ts = TS_BASE + 2 * 60_000
        end_ts = TS_BASE + 7 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3, start_time=start_ts, end_time=end_ts)
        assert len(result) == 3
        assert result[0].timestamp == TS_BASE + 5 * 60_000

    def test_fetch_ohlcv_no_time_params_unchanged(self) -> None:
        client = _client_with_data(10)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3)
        assert len(result) == 3
        assert result[-1].timestamp == TS_BASE + 5 * 60_000


class TestBacktestClientAutoLoad:
    def test_auto_load_data_from_store(self) -> None:
        klines = _make_klines(10)
        store = KlineDataStore()

        with patch.object(store, 'ensure_data', return_value="data/ETHUSDT_1m_20250101_0s_20250201.csv"):
            with patch.object(store, 'load_csv', return_value=klines):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                )

        client.update_current_timestamp(TS_BASE + 5 * 60_000)
        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3)
        assert len(result) == 3
        assert result[-1].timestamp == TS_BASE + 5 * 60_000

    def test_auto_load_with_extra_timeframes(self) -> None:
        klines_1m = _make_klines(10)
        klines_1d = _make_klines(5)

        store = KlineDataStore()
        with patch.object(store, 'ensure_data', return_value="data/mock.csv"):
            with patch.object(store, 'load_csv', side_effect=[klines_1m, klines_1d]):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                    extra_timeframes=('1d',),
                )

        client.update_current_timestamp(TS_BASE + 3 * 60_000)
        result_1d = client.fetch_ohlcv(SYMBOL, '1d', limit=3)
        assert len(result_1d) == 3

    def test_no_data_store_falls_back_to_empty(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        result = client.fetch_ohlcv(SYMBOL, '1m')
        assert result == []

    def test_get_all_klines(self) -> None:
        klines = _make_klines(10)
        store = KlineDataStore()
        with patch.object(store, 'ensure_data', return_value="data/mock.csv"):
            with patch.object(store, 'load_csv', return_value=klines):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                )

        all_klines = client.get_all_klines()
        assert len(all_klines) == 10
        assert all_klines[0].timestamp == TS_BASE
