from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="hype", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=2,
        grid_spacing_rate=0.01,
        max_order=0,
        lowest_price=15,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        signal_min_take_profit_rate=0.01,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.02,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)
