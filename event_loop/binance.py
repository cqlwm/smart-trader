import json
from typing import Any
import websocket
import logging
import random

from event_loop.base import DataEventLoop
from event_loop.handler.binance_kline_parser import BinanceKlineParser

logger = logging.getLogger(__name__)


class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2

    def __init__(self, kline_subscribes: list[str]) -> None:
        super().__init__()
        self.kline_subscribes = kline_subscribes
        self._parser = BinanceKlineParser()

    def start(self) -> None:
        websocket_url = "wss://fstream.binance.com/stream"
        self.ws_session = websocket.WebSocketApp(websocket_url,
                                            on_open=self.on_open,
                                            on_message=self.on_message,
                                            on_error=lambda _, e: logger.error('BinanceEL: %s', e),
                                            on_close=self.on_close,
                                            on_pong=self.on_pong
                                            )
        self.ws_session.run_forever(ping_interval=20, ping_timeout=15)

    def close(self) -> None:
        if hasattr(self, 'ws_session') and self.ws_session:
            self.ws_session.close()

    def add_kline_subscribe(self, subscribe: str) -> None:
        if subscribe not in self.kline_subscribes:
            self.kline_subscribes.append(subscribe)
            if hasattr(self, 'ws_session') and self.ws_session and self.ws_session.sock:
                params: dict[str, Any] = {
                    "method": "SUBSCRIBE",
                    "params": [subscribe],
                    "id": self.SUBSCRIBE_KLINE_ID,
                }
                self.ws_session.send(json.dumps(params))
                logger.info("Dynamically subscribed: %s", subscribe)

    def remove_kline_subscribe(self, subscribe: str) -> None:
        if subscribe in self.kline_subscribes:
            self.kline_subscribes.remove(subscribe)
            if hasattr(self, 'ws_session') and self.ws_session and self.ws_session.sock:
                params: dict[str, Any] = {
                    "method": "UNSUBSCRIBE",
                    "params": [subscribe],
                    "id": self.SUBSCRIBE_KLINE_ID,
                }
                self.ws_session.send(json.dumps(params))
                logger.info("Dynamically unsubscribed: %s", subscribe)

    def _subscribe(self, ws: websocket.WebSocket) -> None:
        params: dict[str, Any] = {
            "method": "SUBSCRIBE",
            "params": self.kline_subscribes,
            "id": self.SUBSCRIBE_KLINE_ID
        }
        ws.send(json.dumps(params))
        logger.info("BinanceDataEventLoop Subscribed: %s", self.kline_subscribes)

    def on_message(self, ws: websocket.WebSocket, message: str) -> None:
        kline_event = self._parser.parse(message)
        if kline_event:
            self.loop(kline_event)

    def on_close(self, ws: websocket.WebSocket, close_status_code: int | str, close_msg: str) -> None:
        logger.warning("BinanceDataEventLoop Closed: %s: %s", close_status_code, close_msg)
        self.stop()

    def on_open(self, ws: websocket.WebSocket) -> None:
        logger.info("BinanceDataEventLoop Opened")
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

    def on_pong(self, ws: websocket.WebSocket, message: str) -> None:
        logger.debug("Pong")
        if random.randint(1, 100) == 1:
            self._subscribe(ws)
