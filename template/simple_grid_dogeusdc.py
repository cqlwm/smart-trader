from client.ex_client import ExSwapClient
import log
from model import OrderSide, PositionSide, Symbol
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import SimpleGridStrategy, SimpleGridStrategyConfig
from template.template_model import TemplateModel
from config import DATA_PATH
logger = log.getLogger(__name__)


def template_1(exchange_client: ExSwapClient) -> TemplateModel:
    """
    简单网格策略模板
    """
    symbol=Symbol(base="doge", quote="usdc")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=0.25099,
        lower_price=0.21500,
        grid_num=58,
        quantity_per_grid=35,
        position_side=PositionSide.SHORT,
        master_order_side=OrderSide.SELL,
        active_grid_count=6,
        delay_pending_order=False,
        initial_quota=11700,
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return TemplateModel(
        symbol=symbol,
        timeframe='1m',
        strategy_v2=simple_grid_strategy,
        strategy_task=StrategyTask(symbol=symbol, strategy=simple_grid_strategy),
    )


def template_2(exchange_client: ExSwapClient) -> TemplateModel:
    """
    简单网格策略模板
    """
    symbol=Symbol(base="doge", quote="usdc")
    config = SimpleGridStrategyConfig(
        symbol=symbol,
        upper_price=0.48502,
        lower_price=0.25952,
        grid_num=169,
        quantity_per_grid=20,
        position_side=PositionSide.SHORT,
        master_order_side=OrderSide.SELL,
        active_grid_count=6,
        delay_pending_order=False,
        initial_quota=11700,
        backup_file=f"{DATA_PATH}/backup_dogeusdc_short_sell_25952_48502.json",
    )
    simple_grid_strategy = SimpleGridStrategy(ex_client=exchange_client, config=config)
    return TemplateModel(
        symbol=symbol,
        timeframe='1m',
        strategy_v2=simple_grid_strategy,
        strategy_task=StrategyTask(symbol=symbol, strategy=simple_grid_strategy),
    )