from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import SimpleGridStrategy, SimpleGridStrategyConfig
from template.template_model import TemplateModel
logger = log.getLogger(__name__)


def template(exchange_client: ExSwapClient) -> TemplateModel:
    """
    简单网格策略模板
    """
    symbol=Symbol(base="DOGE", quote="USDT")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=0.28908,
        lower_price=0.19136,
        grid_num=300,
        quantity_per_grid=100,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=5,
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return TemplateModel(
        symbol=symbol,
        timeframe='1m',
        strategy_v2=simple_grid_strategy,
        strategy_task=StrategyTask(strategy=simple_grid_strategy),
    )
