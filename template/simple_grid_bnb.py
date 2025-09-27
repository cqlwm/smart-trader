from client.ex_client import ExSwapClient
import log
from model import PositionSide, Symbol
from task.strategy_task import StrategyTask
from strategy.simple_grid_strategy_v2 import SimpleGridStrategy

logger = log.getLogger(__name__)


def template(exchange_client: ExSwapClient, symbol: Symbol, timeframe: str) -> StrategyTask:
    """
    简单网格策略模板
    """
    simple_grid_strategy = SimpleGridStrategy(
        ex_client=exchange_client,
        symbol=symbol,
        upper_price=0.28908,
        lower_price=0.19136,
        grid_num=300,
        quantity_per_grid=100,
        position_side=PositionSide.LONG,
        active_grid_count=5,
    )
    return StrategyTask(strategy=simple_grid_strategy)
