import pytest

from model import Symbol, Kline
from event_loop.event import KlineEvent
from event_loop.handler.kline_handler import KlineHandler
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.backtest_client import BacktestClient
from persistence.order_repository import InMemoryOrderRepository
from strategy import GeneralStrategy
from client.ex_client import ExClient


SYMBOL = Symbol(base='ETH', quote='USDT')
TS_BASE = 1_700_000_000_000


class MockExClient(ExClient):
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int) -> list[Kline]:
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


class TestBacktestEventLoopIntegration:
    def test_passes_kline_events_without_json_serialization(self) -> None:
        """BacktestEventLoop should pass KlineEvent directly to handlers,
        without the old Kline→JSON→Kline roundtrip."""
        klines = _make_klines(10)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)

        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_index=0,
        )
        event_loop.add_handler(handler)
        event_loop.start()

        assert len(strategy.received_klines) == 10
        for i, received in enumerate(strategy.received_klines):
            assert received.symbol == SYMBOL
            assert received.close == 2005.0 + i
            assert received.timestamp == TS_BASE + i * 60_000

    def test_backtest_client_price_updates(self) -> None:
        klines = _make_klines(5)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)

        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_index=0,
        )
        event_loop.set_backtest_client(client)
        event_loop.add_handler(handler)
        event_loop.start()

        assert client.get_current_price(SYMBOL) == 2005.0 + 4
        assert client.current_timestamp == TS_BASE + 4 * 60_000

    def test_progress_tracking(self) -> None:
        klines = _make_klines(10)
        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_index=5,
        )
        event_loop.start()

        assert event_loop.is_completed is True
        assert event_loop.progress == 1.0

    def test_start_timestamp(self) -> None:
        klines = _make_klines(10)
        target_ts = TS_BASE + 3 * 60_000

        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)

        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_timestamp=target_ts,
        )
        event_loop.add_handler(handler)
        event_loop.start()

        assert len(strategy.received_klines) == 7
        assert strategy.received_klines[0].timestamp == target_ts
