from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

def short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="doge", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=42,
        grid_spacing_rate=0.004,
        max_order=10,
        # highest_price=1,
        # lowest_price=0,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.004,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def template_long(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="doge", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=42,
        grid_spacing_rate=0.002,
        # max_order=20,
        # highest_price=1,
        # lowest_price=0,
        enable_exit_signal=False,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.004,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)


def template_short(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="doge", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=42,
        grid_spacing_rate=0.0001,
        # max_order=20,
        # highest_price=1,
        # lowest_price=0,
        enable_exit_signal=False,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.004,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_reverse_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)