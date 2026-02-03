from client.ex_client import ExSwapClient
from config import DATA_PATH
import log
from model import OrderSide, PositionSide, Symbol
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import (
    SimpleGridStrategy,
    SimpleGridStrategyConfig,
)
from template.template_model import TemplateModel

logger = log.getLogger(__name__)


def short_grid(exchange_client: ExSwapClient) -> StrategyTask:
    symbol = Symbol(base="xau", quote="usdt")
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