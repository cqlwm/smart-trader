from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from strategy.simple_grid_strategy_v2 import SimpleGridStrategy, SimpleGridStrategyConfig
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

long_position_open_price = 1075.699

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="bnb", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.02,
        grid_spacing_rate=0.1,
        # max_order=20,
        highest_price=long_position_open_price,
        lowest_price=100,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.15,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.15,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="bnb", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=0.01,
        grid_spacing_rate=0.01,
        max_order=0,
        highest_price=2000.1,
        lowest_price=long_position_open_price,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.01,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.05,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)



def simple_grid_long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    """
    简单网格策略模板
    """
    symbol=Symbol(base="bnb", quote="usdt")
    timeframe='1m'
    upper_price=1006.27
    lower_price=891.51
    grid_num=26

    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=upper_price,
        lower_price=lower_price,
        grid_num=grid_num,
        quantity_per_grid=0.01,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=8,
        delay_pending_order=True,
        initial_quota=0,
        backup_file=f'{DATA_PATH}/simple_grid_long_buy_{symbol.simple()}_{int(lower_price)}_{int(upper_price)}_{grid_num}.json',
    )

    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=simple_grid_strategy)