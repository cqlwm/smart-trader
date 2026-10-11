import json
import logging

import pytest

from smart_trader.data_event_loop import (
    DataEventLoop,
    Task,
    parse_kline_message,
    parse_ws_message,
)
from smart_trader.model import Kline, Symbol
from tests.fakes import make_kline, make_ws_kline_message

SYMBOL = Symbol(base="DOGE", quote="USDT")


class RecordingTask(Task):
    def __init__(self, name: str, error: Exception | None = None) -> None:
        super().__init__(name)
        self.error = error
        self.received: list[Kline] = []

    def run(self, kline: Kline) -> None:
        if self.error is not None:
            raise self.error
        self.received.append(kline)


def drain(loop: DataEventLoop) -> None:
    """等待线程池中的任务执行完毕"""
    loop.executor.shutdown(wait=True)


def build_loop(*tasks: Task) -> DataEventLoop:
    """构建处于运行状态的循环(未 start 的循环会丢弃消息)"""
    loop = DataEventLoop()
    for task in tasks:
        loop.add_task(task)
    loop.start()
    return loop


def test_delivers_kline_to_every_task():
    first, second = RecordingTask("first"), RecordingTask("second")
    loop = build_loop(first, second)
    kline = make_kline()

    loop.loop(kline)
    drain(loop)

    assert first.received == [kline], "每个任务都应收到同一个 Kline 对象"
    assert second.received == [kline]


def test_failing_task_does_not_block_others():
    failing = RecordingTask("failing", error=RuntimeError("策略内部错误"))
    healthy = RecordingTask("healthy")
    loop = build_loop(failing, healthy)

    loop.loop(make_kline())
    drain(loop)

    assert len(healthy.received) == 1, "一个任务失败不能影响其它任务"


def test_failing_task_error_is_logged(caplog: pytest.LogCaptureFixture):
    loop = build_loop(RecordingTask("broken", error=RuntimeError("策略内部错误")))

    with caplog.at_level(logging.ERROR):
        loop.loop(make_kline())
        drain(loop)

    assert any(
        "broken" in record.message and record.levelno >= logging.ERROR
        for record in caplog.records
    ), "任务异常必须带任务名记录日志, 不能被线程池静默吞掉"


def test_no_tasks_is_a_noop():
    loop = build_loop()

    loop.loop(make_kline())
    drain(loop)


def test_klines_are_dropped_after_stop():
    """stop() 之后到达的 K 线必须被丢弃, 不能写入已关闭的线程池"""
    task = RecordingTask("t")
    loop = build_loop(task)
    loop.stop()

    loop.loop(make_kline())

    assert task.received == []


class TestParseKlineMessage:
    def test_parses_symbol_timeframe_and_fields(self):
        kline = parse_kline_message(
            json.loads(make_ws_kline_message())["data"]["k"], "dogeusdt@kline_1m"
        )

        assert kline is not None
        assert kline.symbol == SYMBOL, "交易对应从 stream 名解析"
        assert kline.timeframe == "1m"
        assert kline.close == 0.15
        assert kline.finished is True
        assert kline.timestamp == 1_700_000_000_000

    def test_marks_unfinished_kline(self):
        kline = parse_kline_message(
            json.loads(make_ws_kline_message(finished=False))["data"]["k"],
            "dogeusdt@kline_1m",
        )

        assert kline is not None and kline.finished is False

    def test_returns_none_for_unparsable_stream(self):
        """无法解析的 stream 不应抛异常, 否则会打断事件循环"""
        kline = parse_kline_message(
            json.loads(make_ws_kline_message())["data"]["k"], "unknown@stream"
        )

        assert kline is None

    def test_returns_none_for_missing_fields(self):
        kline = parse_kline_message({}, "dogeusdt@kline_1m")

        assert kline is None


class TestParseWsMessage:
    def test_parses_envelope_into_kline(self):
        kline = parse_ws_message(make_ws_kline_message())

        assert kline is not None
        assert kline.symbol == SYMBOL
        assert kline.timeframe == "1m"
        assert kline.finished is True

    def test_returns_none_for_bad_json(self):
        assert parse_ws_message("this is not json") is None

    def test_returns_none_for_non_kline_stream(self):
        message = json.dumps({"stream": "dogeusdt@trade", "data": {}})

        assert parse_ws_message(message) is None

    def test_returns_none_for_missing_kline_body(self):
        message = json.dumps({"stream": "dogeusdt@kline_1m", "data": {}})

        assert parse_ws_message(message) is None
