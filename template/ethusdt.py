from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.alpha_trend_strategy import AlphaTrendStrategy, AlphaTrendStrategyConfig
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdt")
    timeframe='15m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.02,
        grid_spacing_rate=0.1,
        max_order=24,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.15,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.15,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
        
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.02,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)


def alpha_trend(exchange_client: ExSwapClient) -> StrategyTask:
    symbol = Symbol(base="eth", quote="usdt")
    timeframes = ["15m", "5m"]  # Main timeframe first, then auxiliary

    config = AlphaTrendStrategyConfig(
        symbol=symbol,
        timeframes=timeframes,
        position_size=0.01,
        stop_loss_rate=0.02,
        distance_threshold=0.02,
        atr_multiple=1.0,
        period=8,
        signal_reverse=False,
        enable_short_trades=True,
        enable_long_trades=True,
        backup_file_path=f'{DATA_PATH}/alpha_trend_{symbol.simple()}.json',
    )
    strategy = AlphaTrendStrategy(exchange_client, config)

    return StrategyTask(symbol=symbol, strategy=strategy)
