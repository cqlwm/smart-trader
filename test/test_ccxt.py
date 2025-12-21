import ccxt
from ccxt.base.types import ConstructorArgs

exchange = ccxt.binance(ConstructorArgs(
    options={
        "defaultType": "future",
    }
))
exchange.load_markets()
ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1m', limit=10)
print(ohlcv)
