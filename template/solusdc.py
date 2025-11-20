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



def reverse_strategy(exchange_client: ExSwapClient) -> List[StrategyTask]:

    symbol=Symbol(base="sol", quote="usdc")
    timeframe='1m'
    per_order_qty = 0.05
    grid_spacing_rate = 0.0001
    max_order = 10

    long_strategy = SignalGridStrategy(SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=per_order_qty,
        grid_spacing_rate=grid_spacing_rate,
        max_order=max_order,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.5,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.5,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    ), exchange_client)

    short_strategy = SignalGridStrategy(SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=per_order_qty,
        grid_spacing_rate=grid_spacing_rate,
        max_order=max_order,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        signal_min_take_profit_rate=0.5,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.5,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    ), exchange_client)

    def _close_position(strategy: SignalGridStrategy):
        strategy.close_position = True

    long_strategy.on_stop_loss_order_all = lambda: _close_position(short_strategy)
    short_strategy.on_stop_loss_order_all = lambda: _close_position(long_strategy)

    return [
        StrategyTask(symbol=symbol, timeframe=timeframe, strategy=long_strategy),
        StrategyTask(symbol=symbol, timeframe=timeframe, strategy=short_strategy)
    ]



