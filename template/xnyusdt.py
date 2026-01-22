from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

# XNYUSDT

def long_buy_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="xny", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=2000,
        grid_spacing_rate=0.0001,
        max_order=50,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.05,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.05,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_position_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)

