from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import (
    SimpleGridStrategy,
    SimpleGridStrategyConfig,
)
from template.template_model import TemplateModel

logger = log.getLogger(__name__)


def template(exchange_client: ExSwapClient) -> StrategyTask:
    """
    简单网格策略模板
    """
    symbol = Symbol(base="XAU", quote="USDT")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=5200,
        lower_price=4000,
        grid_num=10,
        quantity_per_grid=0.002,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=8,
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config, timeframe="1m")
    return StrategyTask(symbol=symbol, strategy=simple_grid_strategy)
