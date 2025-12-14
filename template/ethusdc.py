from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.scalping_strategy import ScalpingStrategy, ScalpingStrategyConfig
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdc")
    timeframe='15m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.1,
        grid_spacing_rate=-0.1,
        max_order=10,
        signal=AlphaTrendSignal(OrderSide.BUY),
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',

        enable_exit_signal=True,
        exit_signal_take_profit_min_rate=0.002,

        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.04,
        
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.02,

        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdc")
    timeframe='15m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=0.1,
        grid_spacing_rate=-0.1,
        max_order=10,
        signal=AlphaTrendSignal(OrderSide.SELL),
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',

        enable_exit_signal=True,
        exit_signal_take_profit_min_rate=-0.02,

        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.04,
        
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.02,

        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)


def scalping(exchange_client: ExSwapClient) -> StrategyTask:
    timeframe='1m'
    symbol=Symbol(base="eth", quote="usdc")

    config = ScalpingStrategyConfig(
        symbol=symbol,
        position_size=0.1,  # Small position size for scalping
        max_positions=2,  # Allow up to 2 concurrent positions
        stop_loss_rate=0.0129,  # 1.29% stop loss
        take_profit_rate=0.02,  # 2% take profit
        atr_multiple=1,  # AlphaTrend ATR multiplier
        period=8,  # AlphaTrend period
        signal_reverse=True,  # Reverse the signal direction
        enable_short_trades=True,  # Allow both long and short trades
        enable_long_trades=True,
        backup_file_path=f'{DATA_PATH}/scalping_{symbol.simple()}_{timeframe}.json',
    )
    strategy = ScalpingStrategy(exchange_client, config)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)
