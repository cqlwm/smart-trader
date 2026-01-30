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
    symbol = Symbol(base="xau", quote="usdt")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=5400.00,
        lower_price=4800.00,
        grid_num=10,
        quantity_per_grid=0.002,
        position_side=PositionSide.SHORT,
        master_order_side=OrderSide.SELL,
        active_grid_count=10,
        delay_pending_order=True,
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config, timeframe="1m")
    return StrategyTask(symbol=symbol, strategy=simple_grid_strategy)
