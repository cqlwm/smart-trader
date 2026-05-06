import pytest

from model import Symbol, Kline
from event_loop.event import KlineEvent
from event_loop.handler.kline_handler import KlineHandler
from backtest.backtest_event_loop import BacktestEventLoop, _parse_date_to_timestamp
from backtest.types import BacktestConfig
from backtest.backtest_client import BacktestClient
from persistence.order_repository import InMemoryOrderRepository
from strategy import GeneralStrategy
from client.ex_client import ExClient


SYMBOL = Symbol(base='ETH', quote='USDT')
TS_BASE = 1_700_000_000_000
START_DATE = '2023-11-14'
END_DATE = '2023-11-15'


class MockExClient(ExClient):
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100, start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        return []

    def balance(self, coin: str) -> float:
        return 0.0

    def cancel(self, custom_id: str, symbol: Symbol) -> dict:
        return {}

    def query_order(self, custom_id: str, symbol: Symbol) -> dict:
        return {}


class CollectorStrategy(GeneralStrategy):
    def __init__(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        super().__init__(symbols, timeframes)
        self.mock_client = MockExClient()
        self.received_klines: list[Kline] = []

    def exchange_client(self) -> ExClient:
        return self.mock_client

    def run(self, kline: Kline) -> None:
        self.received_klines.append(kline)


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


def _make_config(**overrides) -> BacktestConfig:
    defaults = dict(
        symbol=SYMBOL,
        timeframe='1m',
        start_date=START_DATE,
        end_date=END_DATE,
    )
    return BacktestConfig(**(defaults | overrides))


def _make_client_with_klines(klines: list[Kline]) -> BacktestClient:
    client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
    client._store_klines(SYMBOL, '1m', klines)
    return client


class TestBacktestEventLoopIntegration:
    def test_passes_kline_events_without_json_serialization(self) -> None:
        klines = _make_klines(10)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(_make_client_with_klines(klines))
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.add_handler(handler)
        event_loop.start()

        assert len(strategy.received_klines) == 10

    def test_backtest_client_price_updates(self) -> None:
        klines = _make_klines(5)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)
        client = _make_client_with_klines(klines)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.add_handler(handler)
        event_loop.start()

        assert client.get_current_price(SYMBOL) == 2005.0 + 4
        assert client.current_timestamp == TS_BASE + 4 * 60_000

    def test_progress_tracking(self) -> None:
        klines = _make_klines(10)
        client = _make_client_with_klines(klines)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.start()

        assert event_loop.is_completed is True
        assert event_loop.progress == 1.0

    def test_end_date_limits_replay(self) -> None:
        """end_date should stop the replay before all klines are processed"""
        daily_klines = [
            Kline(
                symbol=SYMBOL, timeframe='1d',
                open=2000.0 + i, high=2010.0 + i,
                low=1990.0 + i, close=2005.0 + i,
                volume=100.0,
                timestamp=TS_BASE + i * 86_400_000,
                finished=True,
            )
            for i in range(10)
        ]
        config = BacktestConfig(
            symbol=SYMBOL, timeframe='1d',
            start_date='2023-11-14',
            end_date='2023-11-17',
        )
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1d'])
        handler = KlineHandler(strategy)
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
        client._store_klines(SYMBOL, '1d', daily_klines)

        event_loop = BacktestEventLoop(config=config)
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1d'])
        event_loop.add_handler(handler)
        event_loop.start()

        assert len(strategy.received_klines) == 3

    def test_fetch_ohlcv_limit_zero_returns_all(self) -> None:
        klines = _make_klines(200)
        client = _make_client_with_klines(klines)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=0, start_time=0)
        assert len(result) == 200
