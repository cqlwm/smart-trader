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
    symbol=Symbol(base="BNB", quote="USDC")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=1480.38,
        lower_price=486.04,
        grid_num=200,
        quantity_per_grid=0.01,
        position_side=PositionSide.LONG,
        master_order_side=OrderSide.BUY,
        active_grid_count=8,
        delay_pending_order=False,
        initial_quota=1
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return TemplateModel(
        symbol=symbol,
        timeframe='1m',
        strategy_v2=simple_grid_strategy,
        strategy_task=StrategyTask(strategy=simple_grid_strategy),
    )
