from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
from task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

long_position_open_price = 1122.730

def template_long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="bnb", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.01,
        grid_spacing_rate=0.01,
        max_order=10,
        highest_price=long_position_open_price,
        lowest_price=100,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        signal_min_take_profit_rate=0.01,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.05,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, timeframe=timeframe, strategy=strategy)

def template_short_sell(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="bnb", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=0.01,
        grid_spacing_rate=0.01,
        max_order=10,
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