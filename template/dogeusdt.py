from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.config import DATA_PATH
from smart_trader.model import OrderSide, PositionSide, Symbol
from smart_trader.strategy.alpha_trend_signal.alpha_trend_grids_signal import (
    AlphaTrendGridsSignal,
)
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.grids_strategy_v2 import (
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol_ = Symbol(base="doge", quote="usdc")
timeframe_ = "1m"

def grid_reverse_long(exchange_client: ExSwapClient) -> StrategyTask:
    return  grid_reverse(exchange_client, PositionSide.LONG, OrderSide.BUY)

def grid_reverse_short(exchange_client: ExSwapClient) -> StrategyTask:
    return  grid_reverse(exchange_client, PositionSide.SHORT, OrderSide.SELL)

def grid_reverse(exchange_client: ExSwapClient, pside: PositionSide, oside: OrderSide) -> StrategyTask:
    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=pside,
        master_side=oside,
        per_order_qty=250,
        grid_spacing_rate=0.0001,
        max_order=50,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.002,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.005,
        order_file_path=f"{DATA_PATH}/signal_grid_{pside.value}_{oside.value}_{symbol_.simple()}_{timeframe_}.json",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)

