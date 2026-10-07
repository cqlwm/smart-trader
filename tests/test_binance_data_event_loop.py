"""BinanceDataEventLoop 的连接生命周期测试

用内存 ws 会话替换真实连接, 验证重连与订阅逻辑。
"""

import json
import threading
import time
from typing import Any

from smart_trader.data_event_loop import BinanceDataEventLoop, Task


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
        self.received: list[str] = []

    def run(self, data: str) -> None:
        self.received.append(data)


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

    loop.start()
    loop.loop('{"stream": "dogeusdt@kline_1m"}')
    loop.executor.shutdown(wait=True)

    assert task.received == ['{"stream": "dogeusdt@kline_1m"}']


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
