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

def grid(exchange_client: ExSwapClient) -> StrategyTask:
    position_side=PositionSide.SHORT
    master_side=OrderSide.SELL

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=1000,
        grid_spacing_rate=-0.1,
        max_order=20,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.05,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.1,
        order_file_path=f"{DATA_PATH}/signal_grid_{position_side}_{master_side}_{symbol_.simple()}_{timeframe_}.json",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.005
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)
