import ccxt
import pandas as pd


def fetch_ohlcv(
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "4h",
    limit: int = 500,
    exchange_id: str = "binanceusdm",
) -> pd.DataFrame:
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({"enableRateLimit": True})
    if hasattr(exchange, "load_markets"):
        exchange.load_markets()

    raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    df = pd.DataFrame(raw, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
    df = df.reset_index(drop=True)
    return df
