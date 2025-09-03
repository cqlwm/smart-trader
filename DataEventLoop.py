import concurrent.futures
from datetime import datetime
import json
from typing import List
import websocket
import logging
import random

logger = logging.getLogger(__name__)

class Task:
    def __init__(self):
        self.name: str
        self.is_running: bool = False

    def run0(self, data: str):
        if self.is_running:
            return
        self.is_running = True
        self.run(data)
        self.is_running = False
    
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
            self.executor.submit(task.run0, data)

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
        ws_session.run_forever(ping_interval=20, ping_timeout=15)

    def _subscribe(self, ws):
        params = {
            "method": "SUBSCRIBE",
            "params": self.kline_subscribes,
            "id": self.SUBSCRIBE_KLINE_ID
        }
        ws.send(json.dumps(params))
        logger.info(f"### BinanceDataEventLoop Subscribed ### {self.kline_subscribes}")

    def on_message(self, _, message):
        self.loop(message)

    def on_error(self, _, error):
        logger.error('BinanceDataEventLoop Error: %s', error)

    def on_close(self, _, close_status_code, close_msg):
        logger.warning(f"### BinanceDataEventLoop Closed ### {close_status_code}: {close_msg}")
        self.stop()

    def on_open(self, ws):
        logger.info("### BinanceDataEventLoop Opened ###")
        params = {
            "method": "SET_PROPERTY",
            "params": [
                "combined",
                True
            ],
            "id": self.SET_PROPERTY_ID
        }
        ws.send(json.dumps(params))
        self._subscribe(ws)

    def on_pong(self, ws, message):
        logger.debug("Pong")
        if random.randint(0, 100) < 10:
            self._subscribe(ws)

