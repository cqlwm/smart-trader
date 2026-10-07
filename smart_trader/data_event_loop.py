import concurrent.futures
import json
import random
import time
from collections.abc import Callable
from typing import Any

import websocket

from smart_trader import log

logger = log.getLogger(__name__)


class Task:
    def __init__(self, name: str):
        self.name: str = name

    def run(self, data: str):
        pass

    def safe_run(self, data: str) -> None:
        """
        执行任务并隔离异常。

        任务运行在共享线程池中, 未捕获的异常会被线程池静默吞掉,
        导致策略停止响应却没有任何日志; 这里统一记录后丢弃该条消息。
        """
        try:
            self.run(data)
        except Exception:
            logger.exception("任务 %s 处理消息失败", self.name)


class DataEventLoop:
    def __init__(self):
        self.tasks: list[Task] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.running: bool = False

    def add_task(self, task: Task):
        self.tasks.append(task)

    def loop(self, data: str):
        if not self.running:
            return
        for task in self.tasks:
            self.executor.submit(task.safe_run, data)

    def start(self):
        self.running = True

    def stop(self):
        """停止接收消息并关闭线程池; 由程序入口在退出时调用"""
        self.running = False
        self.executor.shutdown(wait=False)


def create_websocket_session(
    url: str,
    on_open: Callable[[Any], None],
    on_message: Callable[[Any, str], None],
    on_error: Callable[[Any, Exception], None],
    on_close: Callable[[Any, Any, Any], None],
    on_pong: Callable[[Any, str], None],
) -> Any:
    """默认的 ws 会话工厂; 测试中可注入内存实现"""
    return websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_pong=on_pong,
    )


class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2
    WEBSOCKET_URL = "wss://fstream.binance.com/market/stream"

    def __init__(
        self,
        kline_subscribes: list[str],
        session_factory: Callable[..., Any] = create_websocket_session,
        retry_interval: float = 5.0,
        max_connections: int | None = None,
    ):
        """
        @param kline_subscribes: 订阅的流, 形如 dogeusdt@kline_1m
        @param session_factory: ws 会话工厂(测试注入内存实现)
        @param retry_interval: 断线重连间隔(秒)
        @param max_connections: 最多建立多少个连接, None 表示不限
        """
        super().__init__()
        self.kline_subscribes = kline_subscribes
        self.session_factory = session_factory
        self.retry_interval = retry_interval
        self.max_connections = max_connections

    def start(self):
        """建立连接, 断开后自动重连, 直到 stop() 或达到 max_connections"""
        super().start()
        connections = 0
        while self.running:
            if self.max_connections is not None and connections >= self.max_connections:
                logger.info("已达最大连接数 %s, 停止重连", self.max_connections)
                break

            connections += 1
            ws_session = self.session_factory(
                url=self.WEBSOCKET_URL,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_pong=self.on_pong,
            )
            ws_session.run_forever(ping_interval=20, ping_timeout=15)

            if not self.running:
                break
            logger.warning(
                "%ss 后重连(累计第 %s 次连接)", self.retry_interval, connections
            )
            time.sleep(self.retry_interval)

        logger.info("连接循环结束, 累计建立 %s 个连接", connections)

    def _subscribe(self, ws: websocket.WebSocketApp):
        params: dict[str, Any] = {
            "method": "SUBSCRIBE",
            "params": self.kline_subscribes,
            "id": self.SUBSCRIBE_KLINE_ID,
        }
        ws.send(json.dumps(params))
        logger.info("### BinanceDataEventLoop Subscribed ### %s", self.kline_subscribes)

    def on_message(self, ws: websocket.WebSocketApp, message: str):
        self.loop(message)

    def on_error(self, ws: websocket.WebSocketApp, error: Exception):
        logger.error("BinanceDataEventLoop Error: %s", error)

    def on_close(
        self, ws: websocket.WebSocketApp, close_status_code: int | str, close_msg: str
    ):
        """仅记录断开原因: 是否结束由 stop() 决定, start() 负责重连"""
        logger.warning(
            "### BinanceDataEventLoop Closed ### %s: %s",
            close_status_code,
            close_msg,
        )

    def on_open(self, ws: websocket.WebSocketApp):
        logger.info("### BinanceDataEventLoop Opened ###")
        params: dict[str, Any] = {
            "method": "SET_PROPERTY",
            "params": ["combined", True],
            "id": self.SET_PROPERTY_ID,
        }
        ws.send(json.dumps(params))
        self._subscribe(ws)

    def on_pong(self, ws: websocket.WebSocketApp, message: str):
        logger.debug("Pong")
        if random.randint(1, 100) == 1:
            self._subscribe(ws)
