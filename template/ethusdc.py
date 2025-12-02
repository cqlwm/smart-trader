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

def scalping(exchange_client: ExSwapClient) -> StrategyTask:
    timeframe='1m'
    symbol=Symbol(base="eth", quote="usdc")

    config = ScalpingStrategyConfig(
        symbol=symbol,
        position_size=0.008,  # Small position size for scalping
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
