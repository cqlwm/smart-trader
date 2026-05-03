import pytest
from typing import Any

from model import Symbol, Kline
from backtest.config import BacktestConfig
from backtest.result import BacktestResult
from backtest.runner import BacktestRunner
from backtest.backtest_client import BacktestClient
from backtest.analyzer import BacktestAnalyzer
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


class TestBacktestConfig:
    def test_frozen_dataclass(self) -> None:
        config = BacktestConfig(
            strategy_type="signal_grid",
            strategy_config={"param": 1},
            symbol=SYMBOL,
            timeframe="1m",
            start_date="2025-01-01",
            end_date="2025-06-01",
        )
        assert config.strategy_type == "signal_grid"
        assert config.initial_balance == 10000.0
        assert config.maker_fee == 0.0002
        assert config.taker_fee == 0.0004

        with pytest.raises(AttributeError):
            config.initial_balance = 999  # type: ignore[misc]

    def test_custom_fees(self) -> None:
        config = BacktestConfig(
            strategy_type="test",
            strategy_config={},
            symbol=SYMBOL,
            timeframe="5m",
            start_date="2025-01-01",
            end_date="2025-02-01",
            initial_balance=50000.0,
            maker_fee=0.001,
            taker_fee=0.002,
        )
        assert config.initial_balance == 50000.0
        assert config.maker_fee == 0.001
        assert config.taker_fee == 0.002


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

    def test_load_historical_data_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(5)
        client.load_historical_data(SYMBOL, '1m', klines)
        assert len(client.historical_data[f"{SYMBOL.binance()}_1m"]) == 5

    def test_fetch_ohlcv_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)
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
