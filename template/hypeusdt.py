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
    symbol=Symbol(base="hype", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=1,
        grid_spacing_rate=0.002,
        max_order=12,
        lowest_price=24.32945,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
        exit_signal_take_profit_min_rate=0.002,
        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.01,
        order_file_path=f'{DATA_PATH}/signal_grid_short_sell_{symbol.simple()}_{timeframe}_2.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)

def long_buy_rollover(exchange_client: ExSwapClient) -> StrategyTask:
    symbol=Symbol(base="hype", quote="usdt")
    timeframe='1m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=2,
        grid_spacing_rate=0.002,
        max_order=10,
        # highest_price=15,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.5,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.5,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_rollover_{symbol.simple()}_{timeframe}.json',
        position_reverse=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)
