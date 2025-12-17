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
    symbol=Symbol(base="btc", quote="usdc")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.002,
        grid_spacing_rate=0.002,
        max_order=10,
        enable_exit_signal=True,
        signal=AlphaTrendSignal(OrderSide.BUY),
        exit_signal_take_profit_min_rate=-0.1,
        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.0025,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)


def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="btc", quote="usdc")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=0.002,
        grid_spacing_rate=0.002,
        max_order=10,
        enable_exit_signal=True,
        signal=AlphaTrendSignal(OrderSide.SELL),
        exit_signal_take_profit_min_rate=-0.1,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.0025,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',
        enable_max_order_stop_loss=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def scalping_short(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="btc", quote="usdc")
    timeframe='1m'

    config = ScalpingStrategyConfig(
        symbol=symbol,
        position_size=0.005,  # Small position size for scalping
        max_positions=1,  # Allow up to 3 concurrent positions
        stop_loss_rate=0.0067,  # 0.67% stop loss
        take_profit_rate=0.01,  # 1% take profit
        atr_multiple=1,  # AlphaTrend ATR multiplier
        period=8,  # AlphaTrend period
        signal_reverse=True,  # Reverse the signal direction
        enable_short_trades=True,  # Allow both long and short trades
        enable_long_trades=False,
        backup_file_path=f'{DATA_PATH}/scalping_short_{symbol.simple()}_{timeframe}.json',
    )
    strategy = ScalpingStrategy(exchange_client, config)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)


def scalping_long(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="btc", quote="usdc")
    timeframe='1m'

    config = ScalpingStrategyConfig(
        symbol=symbol,
        position_size=0.005,  # Small position size for scalping
        max_positions=1,  # Allow up to 3 concurrent positions
        stop_loss_rate=0.0067,  # 0.67% stop loss
        take_profit_rate=0.01,  # 1% take profit
        atr_multiple=1,  # AlphaTrend ATR multiplier
        period=8,  # AlphaTrend period
        signal_reverse=True,  # Reverse the signal direction
        enable_short_trades=False,  # Allow both long and short trades
        enable_long_trades=True,
        backup_file_path=f'{DATA_PATH}/scalping_long_{symbol.simple()}_{timeframe}.json',
    )
    strategy = ScalpingStrategy(exchange_client, config)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)