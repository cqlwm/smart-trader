"""DataEventLoop 任务异常回收测试: 线程池默认吞掉任务异常, 必须显式记录日志"""

import logging
import threading
import time

import pytest

from smart_trader.data_event_loop import DataEventLoop, Task

LOGGER_NAME = "smart_trader.data_event_loop"


class FailingTask(Task):
    def __init__(self) -> None:
        self.name = "FailingTask"
        self.executed = threading.Event()

    def run(self, data: str) -> None:
        try:
            raise ValueError("boom")
        finally:
            self.executed.set()


class RecordingTask(Task):
    def __init__(self) -> None:
        self.name = "RecordingTask"
        self.received: list[str] = []
        self.lock = threading.Lock()
        self.executed = threading.Event()

    def run(self, data: str) -> None:
        with self.lock:
            self.received.append(data)
        self.executed.set()


def test_loop_logs_task_exception(caplog: pytest.LogCaptureFixture):
    """任务抛出的异常必须记录 ERROR 日志, 而不是被线程池静默吞掉"""
    event_loop = DataEventLoop()
    task = FailingTask()
    event_loop.add_task(task)
    try:
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            event_loop.loop("data")
            assert task.executed.wait(timeout=5), "任务应在5秒内执行"
            # 回调在 future 完成后由工作线程触发, 轮询等待日志落盘
            deadline = time.monotonic() + 2.0
            errors: list[logging.LogRecord] = []
            while time.monotonic() < deadline:
                errors = [
                    record
                    for record in caplog.records
                    if record.levelno >= logging.ERROR
                ]
                if errors:
                    break
                time.sleep(0.01)

        assert errors, "任务异常被线程池吞掉, 必须记录 ERROR 日志"
        assert any("boom" in record.getMessage() for record in errors)
    finally:
        event_loop.stop()


def test_loop_dispatches_data_to_tasks():
    """正常任务不受异常回收机制影响, 数据照常分发"""
    event_loop = DataEventLoop()
    task = RecordingTask()
    event_loop.add_task(task)
    try:
        event_loop.loop("payload")
        assert task.executed.wait(timeout=5), "任务应在5秒内执行"
        with task.lock:
            assert task.received == ["payload"]
    finally:
        event_loop.stop()
