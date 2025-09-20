from client.ex_client import ExSwapClient
from strategy.bidirectional_grid_rotation_strategy import BidirectionalGridRotationStrategy, BidirectionalGridRotationStrategyConfig
import log
from model import OrderSide, Symbol
from task.strategy_task import StrategyTask
from strategy.grids_strategy_v2 import SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH

logger = log.getLogger(__name__)

def template(exchange_client: ExSwapClient, symbol: Symbol, timeframe: str) -> StrategyTask:

    # 网格模式对信号的多空没有要求
    signal = AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY))

    rotation_config = BidirectionalGridRotationStrategyConfig(
        long_strategy_config=SignalGridStrategyConfig(
            symbol=symbol,
            position_side='long',
            master_side=OrderSide.BUY,
            signal=signal,
            order_file_path=f'{DATA_PATH}/grid_rotation_long_buy_{symbol.to_str()}_{timeframe}.json',
        ),
        short_strategy_config=SignalGridStrategyConfig(
            symbol=symbol,
            position_side='short',
            master_side=OrderSide.SELL,
            signal=signal,
            order_file_path=f'{DATA_PATH}/grid_rotation_short_sell_{symbol.to_str()}_{timeframe}.json',
        ),
        default_strategy='long',
        rotation_increment=1,
        config_backup_path=f'{DATA_PATH}/bidirectional_grid_rotation_{symbol.to_str()}_{timeframe}.json',
    )
    strategy = BidirectionalGridRotationStrategy(exchange_client=exchange_client,config=rotation_config)
    strategy.long_strategy.init_kline_nums = 100
    strategy.short_strategy.init_kline_nums = 100
    
    return StrategyTask(strategy)

