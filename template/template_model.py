from model import Symbol
from strategy import SingleTimeframeStrategy
from task.strategy_task import StrategyTask


class TemplateModel:
    def __init__(self, symbol: Symbol, timeframe: str, strategy: SingleTimeframeStrategy, strategy_task: StrategyTask):
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy
        self.strategy_task = strategy_task
