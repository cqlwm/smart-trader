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

def long_buy_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdc")
    timeframe='1m'

    position_reverse=True
    file_name_flag = '_position_reverse' if position_reverse else ''

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.01,
        grid_spacing_rate=0.0001,
        max_order=15,
        # highest_price=3188.41,
        # lowest_price=2618.83,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy{file_name_flag}_{symbol.simple()}_{timeframe}.json',

        enable_exit_signal=False,
        exit_signal_take_profit_min_rate=0.01,

        fixed_rate_take_profit=True,
        take_profit_use_limit_order=False,
        fixed_take_profit_rate=0.01,
        
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.1,

        # enable_trailing_stop=True,
        # trailing_stop_rate=0.02,
        # trailing_stop_activation_profit_rate=0.01,

        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,

        position_reverse=position_reverse,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)

def short_sell_position_reverse(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="eth", quote="usdc")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=0.01,
        grid_spacing_rate=0.01,
        max_order=15,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_position_reverse_{symbol.simple()}_{timeframe}.json',

        enable_exit_signal=True,
        exit_signal_take_profit_min_rate=0.01,

        fixed_rate_take_profit=True,
        take_profit_use_limit_order=False,
        fixed_take_profit_rate=0.01,
        
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.1,

        # enable_trailing_stop=True,
        # trailing_stop_rate=0.02,
        # trailing_stop_activation_profit_rate=0.01,

        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,

        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)
