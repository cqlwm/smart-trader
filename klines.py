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

logger = log.build_logger(__name__)


def __extract_trade_info__(s):
    # 使用正则表达式匹配交易币种、计价币种和时间周期
    match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', s)

    if match:
        trade_currency = match.group(1)
        quote_currency = match.group(2)
        time_period = match.group(3)
        return trade_currency, quote_currency, time_period
    else:
        return None, None, None


def __add_row__(df, new_row, max_rows=2000):
    df.loc[len(df)] = new_row
    if len(df) > max_rows * 2:
        return df.iloc[-max_rows:].reset_index(drop=True)
    return df


class KlineStream:

    def __init__(self):
        self.strategy_mapping = {}
        self.need_resubscribe = False

    def subscribes(self, key: str, strategies: List[Strategy]):
        for s in strategies:
            self.subscribe(key, s)

    # key: solusdt@kline_1m
    def subscribe(self, key: str, strategy: Strategy):
        if key not in self.strategy_mapping:
            self.strategy_mapping[key] = [strategy]
        else:
            self.strategy_mapping[key].append(strategy)

        exchange = ccxt.binance()
        trade_currency, quote_currency, timeframe = __extract_trade_info__(key)
        symbol = f'{trade_currency}/{quote_currency}'.upper()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=300)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1] # 最后一行假设始终都是未完成的，所以去掉最后一行
        df['datetime'] = df['timestamp'].apply(lambda x: datetime.fromtimestamp(x / 1000))

        strategy.df = df
        self.need_resubscribe = True

    def run(self, key: str, new_row):
        if key in self.strategy_mapping:
            strategies = self.strategy_mapping[key]
            for strategy in strategies:
                df = __add_row__(strategy.df, new_row)
                strategy.df = df
                thread = threading.Thread(target=lambda: strategy.run(df))
                thread.start()

    def start(self, is_fstream=True):
        stream(self, is_fstream)


def stream(ks: KlineStream, is_fstream=True):
    def subscribe(ws):
        if ks.need_resubscribe:
            params = {
                "method": "SUBSCRIBE",
                "params": list(ks.strategy_mapping.keys()),
                "id": 1
            }
            ws.send(json.dumps(params))
            ks.need_resubscribe = False
            logger.info(params)

    def on_message(_, message):
        data = json.loads(message)
        logger.debug(message)

        key = data.get('stream', '')
        if '@kline_' not in key:
            return

        kline = data.get('data', {}).get('k', None)
        if kline is None:
            return

        if kline.get('x', False):
            timestamp = kline['t']
            dt = datetime.fromtimestamp(timestamp / 1000)
            open_price = float(kline['o'])
            high_price = float(kline['h'])
            low_price = float(kline['l'])
            close_price = float(kline['c'])
            volume = float(kline['v'])

            row = {
                'datetime': dt,
                'timestamp': timestamp,
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'close': close_price,
                'volume': volume
            }

            logger.info(f'{key}. {dt}. Close: {close_price}')

            try:
                logger.debug(f'ks.run({key}, row)')
                ks.run(key, row)
            except Exception as e:
                logger.error(e)

    def on_error(_, error):
        logger.error(f'on_error: {json.loads(error)}')

    def on_close(_, close_status_code, close_msg):
        logger.warning(f"### closed ### {close_status_code}: {close_msg}")

    def on_open(ws):
        logger.info(f"### opened ###")
        params = {
            "method": "SET_PROPERTY",
            "params": [
                "combined",
                True
            ],
            "id": 5
        }
        ws.send(json.dumps(params))
        subscribe(ws)

    def on_pong(ws, message):
        logger.debug(f"Pong received: {message}")
        subscribe(ws)

    websocket_url = "wss://fstream.binance.com/stream"
    ws_session = websocket.WebSocketApp(websocket_url,
                                        on_open=on_open,
                                        on_message=on_message,
                                        on_error=on_error,
                                        on_close=on_close,
                                        on_pong=on_pong
                                        )
    ws_session.run_forever(ping_interval=20, ping_timeout=15)


def fetch_ohlcv(symbol: str, timeframe: str | None = None, limit: int = 100):
    if '@kline_' in symbol.lower():
        match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', symbol)
        if match:
            symbol = f'{match.group(1)}/{match.group(2)}'
            timeframe = match.group(3)

    if timeframe is None:
        raise ValueError(f'Invalid symbol: {symbol}')
    else:
        exchange = ccxt.binance({
            'options': {'defaultType': 'future'}
        })
        ohlcv = exchange.fetch_ohlcv(symbol.upper(), timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = df['timestamp'].apply(lambda x: datetime.fromtimestamp(x / 1000).strftime('%Y-%m-%d %H:%M:%S'))
        return df

def main():
    symbol = 'btcusdt@kline_1m'
    df = fetch_ohlcv(symbol, limit=10)
    print(df)

if __name__ == '__main__':
    main()
