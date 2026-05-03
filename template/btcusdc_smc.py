from model import OrderSide, Symbol
from client.ex_client import ExSwapClient
from event_loop.handler.kline_handler import KlineHandler
from strategy.smc_signal import SMCIntradayStrategy


def smc_intraday(exchange_client: ExSwapClient) -> KlineHandler:
    symbol = Symbol(base="btc", quote="usdc")
    strategy = SMCIntradayStrategy(
        symbols=[symbol],
        timeframes=["1w", "1d", "5m"],
        ex_client=exchange_client,
        config={
            "symbol": "BTC/USDC",
            "risk_per_trade_pct": 1.0,
            "account_balance": 100.0,
        },
    )
    return KlineHandler(strategy)
