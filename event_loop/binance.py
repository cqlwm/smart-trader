import json
from typing import Any, List
import websocket
import logging
import random

from event_loop.base import DataEventLoop

logger = logging.getLogger(__name__)

class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2

    def __init__(self, kline_subscribes: List[str]):
        super().__init__()
        self.kline_subscribes = kline_subscribes

    def start(self):
        websocket_url = "wss://fstream.binance.com/stream"
        self.ws_session = websocket.WebSocketApp(websocket_url,
                                            on_open=self.on_open,
                                            on_message=self.on_message,
                                            on_error=lambda _, e: logger.error('BinanceEL: %s', e),
                                            on_close=self.on_close,
                                            on_pong=self.on_pong
                                            )
        self.ws_session.run_forever(ping_interval=20, ping_timeout=15)

    def close(self):
        if hasattr(self, 'ws_session') and self.ws_session:
            self.ws_session.close()


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

