from model import Symbol
from strategy import StrategyV2
from task.strategy_task import StrategyTask


class TemplateModel:
    def __init__(self, symbol: Symbol, timeframe: str, strategy_v2: StrategyV2, strategy_task: StrategyTask):
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy_v2 = strategy_v2
        self.strategy_task = strategy_task
