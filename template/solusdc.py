from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask
from typing import List

logger = log.getLogger(__name__)

# 空头滚仓
def long_buy_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:

    symbol=Symbol(base="sol", quote="usdc")
    timeframe='1m'
    per_order_qty = 0.1
    grid_spacing_rate = 0.0001
    max_order = 50

    long_strategy = SignalGridStrategy(SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=per_order_qty,
        grid_spacing_rate=grid_spacing_rate,
        max_order=max_order,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.01,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
        enable_max_order_stop_loss=True,
    ), exchange_client)

    return StrategyTask(symbol=symbol, strategy=long_strategy)

def short_sell_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="sol", quote="usdc")
    timeframe='1m'
    per_order_qty = 0.1
    grid_spacing_rate = 0.0001
    max_order = 50

    short_strategy = SignalGridStrategy(SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=per_order_qty,
        grid_spacing_rate=grid_spacing_rate,
        max_order=max_order,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        exit_signal_take_profit_min_rate=0.01,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
        enable_max_order_stop_loss=True,
    ), exchange_client)

    return StrategyTask(symbol=symbol, strategy=short_strategy)