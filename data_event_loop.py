import concurrent.futures
import json
from typing import Any, List
import websocket
import logging
import random

logger = logging.getLogger(__name__)

class Task:
    def __init__(self):
        self.name: str
    
    def run(self, data: str):
        pass


class DataEventLoop:
    def __init__(self):
        self.tasks: List[Task] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    def add_task(self, task: Task):
        self.tasks.append(task)

    def loop(self, data: str):
        for task in self.tasks:
            self.executor.submit(task.run, data)

    def start(self):
        pass

    def stop(self):
        self.executor.shutdown(wait=False)

class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2

    def __init__(self, kline_subscribes: List[str]):
        super().__init__()
        self.kline_subscribes = kline_subscribes

    def start(self):
        websocket_url = "wss://fstream.binance.com/stream"
        ws_session = websocket.WebSocketApp(websocket_url,
                                            on_open=self.on_open,
                                            on_message=self.on_message,
                                            on_error=self.on_error,
                                            on_close=self.on_close,
                                            on_pong=self.on_pong
                                            )
        ws_session.run_forever(ping_interval=20, ping_timeout=15) # type: ignore[call-arg]

    def _subscribe(self, ws: websocket.WebSocket):
        params: dict[str, Any] = {
            "method": "SUBSCRIBE",
            "params": self.kline_subscribes,
            "id": self.SUBSCRIBE_KLINE_ID
        }
        ws.send(json.dumps(params))
        logger.info(f"### BinanceDataEventLoop Subscribed ### {self.kline_subscribes}")

    def on_message(self, ws: websocket.WebSocket, message: str):
        self.loop(message)

    def on_error(self, ws: websocket.WebSocket, error: Exception):
        logger.error('BinanceDataEventLoop Error: %s', error)

    def on_close(self, ws: websocket.WebSocket, close_status_code: int | str, close_msg: str):
        logger.warning(f"### BinanceDataEventLoop Closed ### {close_status_code}: {close_msg}")
        self.stop()

    def on_open(self, ws: websocket.WebSocket):
        logger.info("### BinanceDataEventLoop Opened ###")
        params: dict[str, Any] = {
            "method": "SET_PROPERTY",
            "params": [
                "combined",
                True
            ],
            "id": self.SET_PROPERTY_ID
        }
        ws.send(json.dumps(params))
        self._subscribe(ws)

    def on_pong(self, ws: websocket.WebSocket, message: str):
        logger.debug("Pong")
        if random.randint(1, 100) == 1:
            self._subscribe(ws)

