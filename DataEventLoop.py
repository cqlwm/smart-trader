from datetime import datetime
import json
from typing import List
import websocket
import logging
import random
import json
import re
import threading
from typing import List

import websocket

from utils import log
import ccxt
import pandas as pd
from datetime import datetime, timezone
from strategy import Strategy

logger = logging.getLogger(__name__)

class Task:
    def __init__(self):
        '''
        Add a task to the event loop.
        :param name: The name of the task. 主要用于查找和移除任务
        :param callback: The callback function to be called when the task is run.
        '''
        self.name: str
    
    def run(self, data: str):
        pass


class DataEventLoop:
    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def loop(self, data: str):
        for task in self.tasks:
            task.run(data)

    def start(self):
        pass

class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2

    # 新增参数，初始化K线数量
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
        logger.error('BinanceDataEventLoop Error', error)

    def on_close(self, _, close_status_code, close_msg):
        logger.warning(f"### BinanceDataEventLoop Closed ### {close_status_code}: {close_msg}")

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


class PrintKlineTask(Task):
    def __init__(self):
        super().__init__()
        self.name = 'PrintKlineTask'

    def run(self, data: str):
        logger.debug(data)

        data_obj = json.loads(data)

        kline_key = data_obj.get('stream', '')
        is_kline = '@kline_' in kline_key
        kline = data_obj.get('data', {}).get('k', None)
        
        if is_kline and kline:
            if kline.get('x', False):
                timestamp = kline['t']
                dt_str = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
                open_price = float(kline['o'])
                high_price = float(kline['h'])
                low_price = float(kline['l'])
                close_price = float(kline['c'])
                volume = float(kline['v'])

                row = {
                    'datetime': dt_str,
                    'timestamp': timestamp,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume
                }

                logger.info(f'{kline_key}. {dt}. Close: {close_price}. {row}')


def main():
    logging.basicConfig(level=logging.INFO)
    kline_subscribes = ['btcusdt@kline_1m', 'ethusdt@kline_1m']
    event_loop = BinanceDataEventLoop(kline_subscribes)
    event_loop.add_task(PrintKlineTask())
    event_loop.start()

if __name__ == '__main__':
    main()
