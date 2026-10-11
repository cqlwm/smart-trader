"""BinanceDataEventLoop 的连接生命周期测试

用内存 ws 会话替换真实连接, 验证重连与订阅逻辑;
on_message 负责 ws 字符串 → Kline 的唯一解析入口, 一并在此覆盖。
"""

import json
import logging
import threading
import time
from typing import Any

import pytest

from smart_trader.data_event_loop import BinanceDataEventLoop, Task
from smart_trader.model import Kline, Symbol
from tests.fakes import make_kline, make_ws_kline_message

SYMBOL = Symbol(base="DOGE", quote="USDT")

# on_message 的 ws 参数由 websocket-client 回调注入, 单测中不使用
_WS: Any = None


class FakeWebSocketSession:
    """内存版 ws 会话: 记录发送内容, 按脚本决定每次连接的结局"""

    def __init__(self, on_open: Any, outcomes: list[bool]) -> None:
        self.on_open = on_open
        self.outcomes = outcomes
        self.sent: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True

    def run_forever(self, **kwargs: Any) -> bool:
        self.on_open(self)
        if not self.outcomes:
            return False
        return self.outcomes.pop(0)

    def subscriptions(self) -> list[str]:
        """提取所有 SUBSCRIBE 请求中的流名"""
        streams: list[str] = []
        for message in self.sent:
            payload = json.loads(message)
            if payload.get("method") == "SUBSCRIBE":
                streams.extend(payload["params"])
        return streams


class RecordingTask(Task):
    def __init__(self) -> None:
        super().__init__("RecordingTask")
        self.received: list[Kline] = []

    def run(self, kline: Kline) -> None:
        self.received.append(kline)


def build_loop(
    outcomes: list[bool],
    max_connections: int | None = None,
) -> tuple[BinanceDataEventLoop, list[FakeWebSocketSession]]:
    """
    @param outcomes: 每次连接的结局(False 表示连接断开)
    @param max_connections: 默认按 outcomes 数量封顶, 避免测试里出现无限重连
    """
    sessions: list[FakeWebSocketSession] = []

    def factory(url: str, on_open: Any, **callbacks: Any) -> FakeWebSocketSession:
        session = FakeWebSocketSession(on_open=on_open, outcomes=outcomes)
        sessions.append(session)
        return session

    loop = BinanceDataEventLoop(
        kline_subscribes=["dogeusdt@kline_1m"],
        session_factory=factory,  # type: ignore[arg-type]
        retry_interval=0.0,
        max_connections=max_connections
        if max_connections is not None
        else len(outcomes),
    )
    return loop, sessions


def test_subscribes_with_combined_stream_flag_on_open():
    loop, sessions = build_loop([False])

    loop.start()

    sent = [json.loads(message) for message in sessions[0].sent]
    assert sent[0]["method"] == "SET_PROPERTY", "必须先开启合并流"
    assert sent[0]["params"] == ["combined", True]
    assert sessions[0].subscriptions() == ["dogeusdt@kline_1m"]


def test_reconnects_after_connection_drops():
    """连接意外断开必须重连: 否则策略会静默停止接收K线"""
    loop, sessions = build_loop([False, False, False])

    loop.start()

    assert len(sessions) == 3, "每次断开都应建立新连接"


def test_resubscribes_on_every_reconnect():
    loop, sessions = build_loop([False, False])

    loop.start()

    for session in sessions:
        assert session.subscriptions() == ["dogeusdt@kline_1m"], (
            "新连接必须重新订阅, 否则收不到数据"
        )


def test_disconnect_does_not_shut_down_executor():
    """断开重连期间任务必须仍然可用, 否则重连后消息无处可去"""
    loop, _ = build_loop([False])
    task = RecordingTask()
    loop.add_task(task)
    kline = make_kline()

    loop.start()
    loop.loop(kline)
    loop.executor.shutdown(wait=True)

    assert task.received == [kline]


def test_on_message_parses_and_fans_out_kline():
    """on_message 是 ws 字符串 → Kline 的唯一解析入口, 解析后分发给所有任务"""
    loop, _ = build_loop([False])
    task = RecordingTask()
    loop.add_task(task)

    loop.start()
    loop.on_message(_WS, make_ws_kline_message())
    loop.executor.shutdown(wait=True)

    assert len(task.received) == 1, "合法K线消息必须分发给任务"
    assert task.received[0].symbol == SYMBOL
    assert task.received[0].timeframe == "1m"


def test_on_message_keeps_working_after_bad_message():
    """脏消息之后, 事件循环必须继续正常分发 —— 否则线上策略会静默失效"""
    loop, _ = build_loop([False])
    task = RecordingTask()
    loop.add_task(task)

    loop.start()
    loop.on_message(_WS, "this is not json")
    loop.on_message(_WS, make_ws_kline_message())
    loop.executor.shutdown(wait=True)

    assert len(task.received) == 1, "脏消息被跳过, 后续消息仍应处理"


def test_on_message_drops_message_without_kline_body():
    loop, _ = build_loop([False])
    task = RecordingTask()
    loop.add_task(task)

    loop.start()
    loop.on_message(_WS, json.dumps({"stream": "dogeusdt@kline_1m", "data": {}}))
    loop.executor.shutdown(wait=True)

    assert task.received == [], "无K线体的消息(如订阅回执)必须忽略"


def test_on_message_logs_unparsable_message(caplog: pytest.LogCaptureFixture):
    loop, _ = build_loop([False])

    with caplog.at_level(logging.DEBUG):
        loop.on_message(_WS, "this is not json")

    assert any(
        "无法解析" in record.message for record in caplog.records
    ), "无法解析的消息必须记录 debug 日志"


def test_stop_ends_the_reconnect_loop():
    loop, sessions = build_loop([False] * 10)
    connections: list[int] = []

    original_on_open = loop.on_open

    def on_open(ws: Any) -> None:
        connections.append(1)
        original_on_open(ws)
        if len(connections) == 2:
            loop.stop()

    loop.on_open = on_open  # type: ignore[method-assign]
    loop.start()

    assert len(sessions) == 2, "显式 stop() 后不应继续重连"


def test_stop_from_another_thread_exits_the_loop():
    """生产环境是无限重连: 必须能被外部 stop()(Ctrl-C)叫停"""
    worker_release = threading.Event()
    sessions: list[FakeWebSocketSession] = []

    def factory(url: str, on_open: Any, **callbacks: Any) -> FakeWebSocketSession:
        session = FakeWebSocketSession(on_open=on_open, outcomes=[False])
        sessions.append(session)
        # 每次断开后停一下, 给主线程 stop() 的机会
        worker_release.wait(0.01)
        return session

    loop = BinanceDataEventLoop(
        kline_subscribes=["dogeusdt@kline_1m"],
        session_factory=factory,  # type: ignore[arg-type]
        retry_interval=0.0,
    )
    loop.session_factory = factory  # type: ignore[assignment]

    def stop_after_a_few_connections() -> None:
        while len(sessions) < 3:
            time.sleep(0.01)
        loop.stop()

    stopper = threading.Thread(target=stop_after_a_few_connections, daemon=True)
    stopper.start()
    loop.start()  # 无限重连配置下也必须能退出
    stopper.join()
    worker_release.set()

    assert loop.running is False
    node_count = len(sessions)
    time.sleep(0.05)
    assert len(sessions) == node_count, "stop() 之后不应再新建连接"
