import pytest
from unittest.mock import patch, MagicMock

from model import Symbol, Kline
from backtest.types import BacktestConfig
from backtest.backtest_runner import BacktestRunner
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
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


class TestBacktestEventLoopSubscribe:
    def test_subscribe_records_pairs(self) -> None:
        el = BacktestEventLoop(config=BacktestConfig(symbol=SYMBOL, timeframe='1m', start_date='2023-11-14', end_date='2023-11-15'))
        el.subscribe(symbols=[SYMBOL], timeframes=['1m', '5m'])
        assert (SYMBOL, '1m') in el._subscriptions.values()
        assert (SYMBOL, '5m') in el._subscriptions.values()

    def test_load_subscribed_klines_from_client(self) -> None:
        klines = _make_klines(5)
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        client._store_klines(SYMBOL, '1m', klines)

        el = BacktestEventLoop(config=BacktestConfig(symbol=SYMBOL, timeframe='1m', start_date='2023-11-14', end_date='2023-11-15'))
        el.set_backtest_client(client)
        el.subscribe(symbols=[SYMBOL], timeframes=['1m'])

        loaded = el._load_subscribed_klines()
        assert len(loaded) == 5

    def test_load_subscribed_klines_skips_unsubscribed(self) -> None:
        klines_1m = _make_klines(5)
        klines_5m = _make_klines(3)
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        client._store_klines(SYMBOL, '1m', klines_1m)
        client._store_klines(SYMBOL, '5m', klines_5m)

        el = BacktestEventLoop(config=BacktestConfig(symbol=SYMBOL, timeframe='1m', start_date='2023-11-14', end_date='2023-11-15'))
        el.set_backtest_client(client)
        el.subscribe(symbols=[SYMBOL], timeframes=['1m'])

        loaded = el._load_subscribed_klines()
        assert len(loaded) == 5  # only 1m klines

    def test_load_subscribed_klines_empty_without_client(self) -> None:
        el = BacktestEventLoop(config=BacktestConfig(symbol=SYMBOL, timeframe='1m', start_date='2023-11-14', end_date='2023-11-15'))
        el.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        assert el._load_subscribed_klines() == []

    def test_start_loads_klines_from_subscriptions(self) -> None:
        klines = _make_klines(5)
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        client._store_klines(SYMBOL, '1m', klines)

        el = BacktestEventLoop(config=BacktestConfig(symbol=SYMBOL, timeframe='1m', start_date='2023-11-14', end_date='2023-11-15'))
        el.set_backtest_client(client)
        el.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        el.start()

        assert len(el.historical_klines) == 5
        assert el.is_completed


class TestBacktestRunnerInit:
    def test_creates_backtest_client(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert isinstance(runner._backtest_client, BacktestClient)

    def test_creates_backtest_event_loop_with_subscribe(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert isinstance(runner._event_loop, BacktestEventLoop)
        assert runner._event_loop.backtest_client is runner._backtest_client
        assert (SYMBOL, '1m') in runner._event_loop._subscriptions.values()

    def test_creates_bot_manager(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            config_path="test_strategies.yaml",
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert runner._bot_manager is not None
        assert runner._bot_manager._config_path == "test_strategies.yaml"


class TestBacktestRunnerRun:
    def test_run_returns_analysis_dict(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        with patch.object(runner._bot_manager, 'start_bot'):
            result = runner.run()

        assert 'summary' in result
        assert 'risk_metrics' in result
        assert 'trade_metrics' in result

    def test_run_calls_bot_start(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        with patch.object(runner._bot_manager, 'start_bot') as mock_start:
            runner.run()
            mock_start.assert_called_once()


class TestBacktestRunnerReport:
    def test_report_calls_bot_start_and_trade_analysis(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        with patch.object(runner._bot_manager, 'start_bot'), \
             patch('backtest.backtest_runner.TradeAnalysis') as mock_ta_cls:
            mock_ta = MagicMock()
            mock_ta.report.return_value = "BACKTEST REPORT\n..."
            mock_ta_cls.return_value = mock_ta

            report = runner.report()

        mock_ta_cls.assert_called_once()
        assert "BACKTEST REPORT" in report
