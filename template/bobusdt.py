from client.ex_client import ExSwapClient
import log
from model import OrderSide, PlaceOrderBehavior, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol=Symbol(base="bob", quote="usdt")
timeframe='1m'

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=600,
        grid_spacing_rate=0.0001,
        max_order=10,
        # enable_exit_signal=True,
        # signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        # signal_min_take_profit_rate=0.005,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.0001,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',
        enable_limit_take_profit=True,
        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,
        # place_order_behavior=PlaceOrderBehavior.CHASER,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=600,
        grid_spacing_rate=-0.5,
        max_order=10,
        # enable_exit_signal=True,
        # signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        # signal_min_take_profit_rate=0.005,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.9,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,
        place_order_behavior=PlaceOrderBehavior.CHASER,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

