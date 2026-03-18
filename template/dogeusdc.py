from datetime import datetime, timezone

from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol_=Symbol(base="doge", quote="usdc")
timeframe_= '5m'
today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")

def short_sell_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=200,
        grid_spacing_rate=0.0001,
        max_order=10,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.005,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_position_reverse_{symbol_.simple()}_{timeframe_}.json',
        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)

def long_buy_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=200,
        grid_spacing_rate=0.0001,
        max_order=10,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.005,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_position_reverse_{symbol_.simple()}_{timeframe_}.json',
        position_reverse=True,
        enable_max_order_stop_loss=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=3500,
        grid_spacing_rate=-0.1,
        max_order=3,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.03,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.03,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol_.simple()}_{timeframe_}_{today_utc}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=3500,
        grid_spacing_rate=-0.1,
        max_order=3,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        exit_signal_take_profit_min_rate=0.03,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.03,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol_.simple()}_{timeframe_}_{today_utc}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)


def market_trend_task(exchange_client: ExSwapClient) -> StrategyTask | None:
    symbols = [
        Symbol(base="btc",  quote="usdc"),
        Symbol(base="eth",  quote="usdc"),
        Symbol(base="sol",  quote="usdc"),
        Symbol(base="doge", quote="usdc"),
    ]

    def prev_day_change(s: Symbol) -> float:
        ohlcv = exchange_client.fetch_ohlcv(s, '1d', limit=2)
        prev = ohlcv[-2]
        return prev.close - prev.open

    changes = [prev_day_change(s) for s in symbols]

    if all(c > 0 for c in changes):
        return long_buy(exchange_client)
    if all(c < 0 for c in changes):
        return short_sell(exchange_client)
    return None
