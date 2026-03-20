from client.ex_client import ExSwapClient
from model import Symbol, OrderSide
from strategy.daily_trend_strategy import DailyTrendStrategy, DailyTrendStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from event_loop.handler.kline_handler import KlineHandler

def doge_daily_trend(exchange_client: ExSwapClient) -> KlineHandler:
    trade_symbol = Symbol(base="doge", quote="usdt")
    trade_timeframe = "5m"
    direction_symbols = [
        Symbol(base="btc", quote="usdt"),
        Symbol(base="eth", quote="usdt"),
        Symbol(base="sol", quote="usdt"),
        Symbol(base="doge", quote="usdt"),
    ]

    config = DailyTrendStrategyConfig(
        trade_symbol=trade_symbol,
        trade_timeframe=trade_timeframe,
        direction_symbols=direction_symbols,
        per_order_qty=100,
        max_daily_orders=3,
        take_profit_rate=0.03,
        stop_loss_rate=0.03,
        order_file_path=f'{DATA_PATH}/daily_trend_{trade_symbol.simple()}_{trade_timeframe}.json',
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY))
    )

    return KlineHandler(DailyTrendStrategy(config, exchange_client))
