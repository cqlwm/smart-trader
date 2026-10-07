import json
import logging

import pytest

from smart_trader.data_event_loop import DataEventLoop, Task


class RecordingTask(Task):
    def __init__(self, name: str, error: Exception | None = None) -> None:
        super().__init__(name)
        self.error = error
        self.received: list[str] = []

    def run(self, data: str) -> None:
        if self.error is not None:
            raise self.error
        self.received.append(data)


class DecodingTask(Task):
    """故意抛异常的任务, 用于验证异常不会逃逸出事件循环"""

    def __init__(self) -> None:
        super().__init__("DecodingTask")
        self.handled = 0

    def run(self, data: str) -> None:
        payload = json.loads(data)
        self.handled += 1
        assert isinstance(payload, dict)


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


def test_delivers_message_to_every_task():
    first, second = RecordingTask("first"), RecordingTask("second")
    loop = build_loop(first, second)

    loop.loop('{"stream": "dogeusdt@kline_1m"}')
    drain(loop)

    assert first.received == ['{"stream": "dogeusdt@kline_1m"}']
    assert second.received == ['{"stream": "dogeusdt@kline_1m"}']


def test_failing_task_does_not_block_others():
    failing = RecordingTask("failing", error=RuntimeError("策略内部错误"))
    healthy = RecordingTask("healthy")
    loop = build_loop(failing, healthy)

    loop.loop('{"stream": "dogeusdt@kline_1m"}')
    drain(loop)

    assert healthy.received == ['{"stream": "dogeusdt@kline_1m"}'], (
        "一个任务失败不能影响其它任务"
    )


def test_bad_message_is_logged_and_loop_survives(
    caplog: pytest.LogCaptureFixture,
):
    """脏消息必须被记录, 且后续消息仍能处理 —— 否则线上策略会静默失效"""
    task = DecodingTask()
    loop = build_loop(task)

    with caplog.at_level(logging.ERROR):
        loop.loop("this is not json")
        drain(loop)

    assert task.handled == 0, "脏消息不应进入任务逻辑"
    assert any(
        "DecodingTask" in record.message and record.levelno >= logging.ERROR
        for record in caplog.records
    ), f"任务失败必须记录错误日志, 实际日志: {[r.message for r in caplog.records]}"


def test_loop_keeps_working_after_a_bad_message():
    """脏消息之后, 事件循环必须继续正常分发"""
    task = DecodingTask()
    loop = build_loop(task)

    loop.loop("this is not json")
    loop.loop('{"stream": "dogeusdt@kline_1m"}')
    drain(loop)

    assert task.handled == 1, "脏消息被跳过, 后续消息仍应处理"


def test_failing_task_error_is_logged(caplog: pytest.LogCaptureFixture):
    loop = build_loop(RecordingTask("broken", error=RuntimeError("策略内部错误")))

    with caplog.at_level(logging.ERROR):
        loop.loop('{"stream": "dogeusdt@kline_1m"}')
        drain(loop)

    assert any(
        "broken" in record.message and record.levelno >= logging.ERROR
        for record in caplog.records
    ), "任务异常必须带任务名记录日志, 不能被线程池静默吞掉"


def test_no_tasks_is_a_noop():
    loop = build_loop()

    loop.loop('{"stream": "dogeusdt@kline_1m"}')
    drain(loop)


def test_messages_are_dropped_after_stop():
    """stop() 之后到达的消息必须被丢弃, 不能写入已关闭的线程池"""
    task = RecordingTask("t")
    loop = build_loop(task)
    loop.stop()

    loop.loop('{"stream": "dogeusdt@kline_1m"}')

    assert task.received == []
