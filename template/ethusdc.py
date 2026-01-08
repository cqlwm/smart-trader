from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.scalping_strategy import ScalpingStrategy, ScalpingStrategyConfig
from strategy.alpha_trend_strategy import AlphaTrendStrategy, AlphaTrendStrategyConfig
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdt")
    timeframe='4h'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.01,
        grid_spacing_rate=-0.5,
        max_order=10,
        # highest_price=3188.41,
        # lowest_price=2618.83,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',

        enable_exit_signal=False,
        exit_signal_take_profit_min_rate=0.1,

        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.1,
        
        enable_order_stop_loss=False,
        order_stop_loss_rate=0.5,

        # enable_trailing_stop=True,
        # trailing_stop_rate=0.02,
        # trailing_stop_activation_profit_rate=0.01,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdc")
    timeframe='15m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
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

    return StrategyTask(symbol=symbol, strategy=strategy)

def scalping(exchange_client: ExSwapClient) -> StrategyTask:
    timeframe='1m'
    symbol=Symbol(base="eth", quote="usdc")

    config = ScalpingStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
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

    return StrategyTask(symbol=symbol, strategy=strategy)


def alpha_trend(exchange_client: ExSwapClient) -> StrategyTask:
    symbol = Symbol(base="eth", quote="usdc")
    timeframes = ["3m", "1m"]  # Main timeframe first, then auxiliary

    config = AlphaTrendStrategyConfig(
        symbol=symbol,
        timeframes=timeframes,
        position_size=0.01,
        stop_loss_rate=0.02,
        atr_multiple=1.0,
        period=8,
        signal_reverse=False,
        enable_short_trades=True,
        enable_long_trades=True,
        backup_file_path=f'{DATA_PATH}/alpha_trend_{symbol.simple()}.json',
    )
    strategy = AlphaTrendStrategy(exchange_client, config)

    return StrategyTask(symbol=symbol, strategy=strategy)
