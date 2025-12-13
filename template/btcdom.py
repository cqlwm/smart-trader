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

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="btcdom", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.002,
        grid_spacing_rate=0.005,
        max_order=20,
        # highest_price=4264.8,
        # lowest_price=0,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.005,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)



def long_buy_simple_grid(exchange_client: ExSwapClient) -> StrategyTask:
    """
    简单网格策略模板
    """
    symbol=Symbol(base="btcdom", quote="usdt")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=5036.6,
        lower_price=4409.7,
        grid_num=45,
        quantity_per_grid=0.002,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=8,
        delay_pending_order=False,
        initial_quota=0.06
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return StrategyTask(symbol=symbol, timeframe='1m', strategy=simple_grid_strategy)
