import json
from typing import Any

from smart_trader.model import Symbol
from smart_trader.strategy import SingleTimeframeStrategy
from smart_trader.task.strategy_task import StrategyTask, parse_kline_message
from tests.fakes import FakeMarketDataPort

SYMBOL = Symbol(base="DOGE", quote="USDT")


def build_message(
    stream: str = "dogeusdt@kline_1m", finished: bool = True, **kline_overrides: Any
) -> str:
    kline: dict[str, Any] = {
        "t": 1_700_000_000_000,
        "o": "0.1",
        "h": "0.2",
        "l": "0.05",
        "c": "0.15",
        "v": "100",
        "x": finished,
    }
    kline.update(kline_overrides)
    return json.dumps({"stream": stream, "data": {"k": kline}})


class RecordingStrategy(SingleTimeframeStrategy):
    def __init__(self) -> None:
        super().__init__("1m", FakeMarketDataPort())
        self.received_klines: list[Any] = []

    def run(self, kline: Any) -> None:
        self.received_klines.append(kline)


class TestParseKlineMessage:
    def test_parses_symbol_timeframe_and_fields(self):
        kline = parse_kline_message(
            json.loads(build_message())["data"]["k"], "dogeusdt@kline_1m"
        )

        assert kline is not None
        assert kline.symbol == SYMBOL, "交易对应从 stream 名解析"
        assert kline.timeframe == "1m"
        assert kline.close == 0.15
        assert kline.finished is True
        assert kline.timestamp == 1_700_000_000_000

    def test_marks_unfinished_kline(self):
        kline = parse_kline_message(
            json.loads(build_message(finished=False))["data"]["k"], "dogeusdt@kline_1m"
        )

        assert kline is not None and kline.finished is False

    def test_returns_none_for_unparsable_stream(self):
        """无法解析的 stream 不应抛异常, 否则会打断事件循环"""
        kline = parse_kline_message(
            json.loads(build_message())["data"]["k"], "unknown@stream"
        )

        assert kline is None

    def test_returns_none_for_missing_fields(self):
        kline = parse_kline_message({}, "dogeusdt@kline_1m")

        assert kline is None


class TestStrategyTask:
    def test_forwards_matching_kline_to_strategy(self):
        strategy = RecordingStrategy()
        task = StrategyTask(SYMBOL, strategy)

        task.run(build_message())

        assert len(strategy.received_klines) == 1
        assert strategy.received_klines[0].symbol == SYMBOL

    def test_ignores_other_symbol(self):
        strategy = RecordingStrategy()
        task = StrategyTask(Symbol(base="BTC", quote="USDT"), strategy)

        task.run(build_message(stream="dogeusdt@kline_1m"))

        assert strategy.received_klines == [], "非本任务交易对的K线必须忽略"

    def test_ignores_other_timeframe(self):
        strategy = RecordingStrategy()
        task = StrategyTask(SYMBOL, strategy)

        task.run(build_message(stream="dogeusdt@kline_5m"))

        assert strategy.received_klines == [], "非本任务周期的K线必须忽略"

    def test_ignores_non_kline_stream(self):
        strategy = RecordingStrategy()
        task = StrategyTask(SYMBOL, strategy)

        task.run(json.dumps({"stream": "dogeusdt@trade", "data": {}}))

        assert strategy.received_klines == []

    def test_task_name_identifies_symbol_and_timeframes(self):
        task = StrategyTask(SYMBOL, RecordingStrategy())

        assert "DOGE/USDT" in task.name
        assert "1m" in task.name
