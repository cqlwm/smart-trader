import os

import ccxt
from ccxt.base.types import ConstructorArgs
import dotenv
dotenv.load_dotenv()

from client.binance_client import BinanceSwapClient

def test_ccxt():
    exchange = ccxt.binance(ConstructorArgs(
        options={
            "defaultType": "future",
        }
    ))
    exchange.load_markets()
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=10)
    print(ohlcv)

def test_balance():
    u = BinanceSwapClient(
        api_key=os.getenv('BINANCE_API_KEY_MAIN'),
        api_secret=os.getenv('BINANCE_API_SECRET_MAIN'),
        is_test=True
    ).balance('USDT')
    print(u)