import json
import pytest

from model import Symbol, Kline
from event_loop.event import Event, KlineEvent
from event_loop.handler.binance_kline_parser import BinanceKlineParser
from event_loop.handler.kline_handler import KlineHandler
from event_loop.base import Handler
from strategy import GeneralStrategy
from client.ex_client import ExClient


SYMBOL = Symbol(base='DOGE', quote='USDC')
TS_BASE = 1_700_000_000_000


def _make_kline(finished: bool = True) -> Kline:
    return Kline(
        symbol=SYMBOL,
        timeframe='1m',
        open=0.08,
        high=0.09,
        low=0.07,
        close=0.085,
        volume=1000.0,
        timestamp=TS_BASE,
        finished=finished,
    )


def _make_binance_ws_message(kline: Kline) -> str:
    ws_data = {
        "stream": kline.symbol.binance_ws_sub_kline(kline.timeframe),
        "data": {
            "e": "kline",
            "E": kline.timestamp,
            "s": kline.symbol.binance(),
            "k": {
                "t": kline.timestamp,
                "T": kline.timestamp + 60000,
                "s": kline.symbol.binance(),
                "i": kline.timeframe,
                "f": 100,
                "L": 200,
                "o": str(kline.open),
                "c": str(kline.close),
                "h": str(kline.high),
                "l": str(kline.low),
                "v": str(kline.volume),
                "n": 100,
                "x": kline.finished,
                "q": str(kline.volume * kline.close),
                "V": str(kline.volume),
                "Q": str(kline.volume * kline.close),
                "B": "0",
            },
        },
    }
    return json.dumps(ws_data)


class TestBinanceKlineParser:
    def test_parse_valid_kline_message(self) -> None:
        parser = BinanceKlineParser()
        kline = _make_kline()
        message = _make_binance_ws_message(kline)

        event = parser.parse(message)

        assert event is not None
        assert isinstance(event, KlineEvent)
        assert event.timestamp == TS_BASE
        assert event.kline.symbol == SYMBOL
        assert event.kline.timeframe == '1m'
        assert event.kline.open == 0.08
        assert event.kline.high == 0.09
        assert event.kline.low == 0.07
        assert event.kline.close == 0.085
        assert event.kline.volume == 1000.0
        assert event.kline.finished is True

    def test_parse_unfinished_kline(self) -> None:
        parser = BinanceKlineParser()
        kline = _make_kline(finished=False)
        message = _make_binance_ws_message(kline)

        event = parser.parse(message)

        assert event is not None
        assert event.kline.finished is False

    def test_parse_non_kline_message_returns_none(self) -> None:
        parser = BinanceKlineParser()
        message = json.dumps({"stream": "some_other_stream", "data": {}})

        event = parser.parse(message)

        assert event is None

    def test_parse_invalid_stream_key_returns_none(self) -> None:
        parser = BinanceKlineParser()
        message = json.dumps({
            "stream": "invalid@kline_1m",
            "data": {"k": {"o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "100", "t": 123, "x": True}},
        })

        event = parser.parse(message)

        assert event is None

    def test_parse_subscribe_response_returns_none(self) -> None:
        parser = BinanceKlineParser()
        message = json.dumps({"result": None, "id": 1})

        event = parser.parse(message)

        assert event is None

    def test_parse_usdt_pair(self) -> None:
        parser = BinanceKlineParser()
        sym = Symbol(base='BTC', quote='USDT')
        kline = Kline(
            symbol=sym, timeframe='5m',
            open=50000.0, high=51000.0, low=49000.0, close=50500.0,
            volume=10.0, timestamp=TS_BASE, finished=True,
        )
        message = _make_binance_ws_message(kline)

        event = parser.parse(message)

        assert event is not None
        assert event.kline.symbol.base == 'BTC'
        assert event.kline.symbol.quote == 'USDT'
        assert event.kline.timeframe == '5m'


class MockExClient(ExClient):
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100, start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        return []

    def balance(self, coin: str) -> float:
        return 0.0

    def cancel(self, custom_id: str, symbol: Symbol) -> dict:
        return {}

    def query_order(self, custom_id: str, symbol: Symbol) -> dict:
        return {}


class SpyStrategy(GeneralStrategy):
    def __init__(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        super().__init__(symbols, timeframes)
        self.mock_client = MockExClient()
        self.received_klines: list[Kline] = []

    def exchange_client(self) -> ExClient:
        return self.mock_client

    def run(self, kline: Kline) -> None:
        self.received_klines.append(kline)


class TestKlineHandler:
    def test_handles_kline_event_for_matching_symbol_and_timeframe(self) -> None:
        strategy = SpyStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)
        kline = _make_kline()
        event = KlineEvent(timestamp=kline.timestamp, kline=kline)

        handler.run(event)

        assert len(strategy.received_klines) == 1
        assert strategy.received_klines[0] is kline

    def test_ignores_non_kline_event(self) -> None:
        strategy = SpyStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)
        event = Event(timestamp=TS_BASE)

        handler.run(event)

        assert len(strategy.received_klines) == 0

    def test_ignores_kline_for_wrong_symbol(self) -> None:
        other_symbol = Symbol(base='BTC', quote='USDT')
        strategy = SpyStrategy(symbols=[other_symbol], timeframes=['1m'])
        handler = KlineHandler(strategy)
        kline = _make_kline()
        event = KlineEvent(timestamp=kline.timestamp, kline=kline)

        handler.run(event)

        assert len(strategy.received_klines) == 0

    def test_ignores_kline_for_wrong_timeframe(self) -> None:
        strategy = SpyStrategy(symbols=[SYMBOL], timeframes=['5m'])
        handler = KlineHandler(strategy)
        kline = _make_kline()
        event = KlineEvent(timestamp=kline.timestamp, kline=kline)

        handler.run(event)

        assert len(strategy.received_klines) == 0
