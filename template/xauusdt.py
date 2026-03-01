from client.ex_client import ExSwapClient
from config import DATA_PATH
import log
from model import OrderSide, PositionSide, Symbol
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.grids_strategy_v2 import SignalGridStrategyConfig, SignalGridStrategy
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import (
    SimpleGridStrategy,
    SimpleGridStrategyConfig,
)

logger = log.getLogger(__name__)

symbol = Symbol(base="xau", quote="usdt")

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    timeframe='5m'

    config=SignalGridStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=0.02,
        grid_spacing_rate=-0.1,
        max_order=10,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.1,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.1,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
        enable_max_order_stop_loss=True,
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol, strategy=strategy)


def short_grid(exchange_client: ExSwapClient) -> StrategyTask:
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=4884.560,
        lower_price=4381.970,
        grid_num=100,
        quantity_per_grid=0.002,
        position_side=PositionSide.SHORT,
        master_order_side=OrderSide.SELL,
        active_grid_count=10,
        delay_pending_order=False,
        initial_quota=0.2,
        backup_file=f"{DATA_PATH}/xauusdt_short_grid_4884_4381.json",
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config, timeframe="1m")
    return StrategyTask(symbol=symbol, strategy=simple_grid_strategy)


def long_grid(exchange_client: ExSwapClient) -> StrategyTask:
    """
    简单网格策略模板
    """
    symbol = Symbol(base="xau", quote="usdt")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=4936.00,
        lower_price=4604.50,
        grid_num=65,
        quantity_per_grid=0.002,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=8,
        delay_pending_order=False,
        initial_quota=0.2,
        backup_file=f"{DATA_PATH}/xauusdt_long_grid_4936_4604.json",
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config, timeframe="1m")
    return StrategyTask(symbol=symbol, strategy=simple_grid_strategy)