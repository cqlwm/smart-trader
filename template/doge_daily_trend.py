from client.ex_client import ExSwapClient
from model import Symbol, OrderSide
from strategy.daily_trend_strategy import DailyTrendStrategy, DailyTrendStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.multi_symbol_strategy_task import MultiSymbolStrategyTask

def doge_daily_trend(exchange_client: ExSwapClient) -> MultiSymbolStrategyTask:
    trade_symbol = Symbol(base="doge", quote="usdt")
    trade_timeframe = "15m"
    direction_symbols = [
        Symbol(base="btc", quote="usdt"),
        Symbol(base="eth", quote="usdt"),
        Symbol(base="sol", quote="usdt"),
        Symbol(base="doge", quote="usdt"),
    ]
    all_symbols = direction_symbols

    signal = AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY))

    config = DailyTrendStrategyConfig(
        trade_symbol=trade_symbol,
        trade_timeframe=trade_timeframe,
        direction_symbols=direction_symbols,
        per_order_qty=42,  # Example qty for doge
        max_daily_orders=3,
        take_profit_rate=0.03, # 固定止盈3%
        stop_loss_rate=0.03,   # 固定止损3%
        order_file_path=f'{DATA_PATH}/daily_trend_{trade_symbol.simple()}_{trade_timeframe}.json',
        signal=signal
    )
    strategy = DailyTrendStrategy(config, exchange_client)

    return MultiSymbolStrategyTask(symbols=all_symbols, strategy=strategy)
