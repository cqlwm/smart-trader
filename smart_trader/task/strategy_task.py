from smart_trader.data_event_loop import Task
from smart_trader.model import Kline, Symbol
from smart_trader.strategy import MultiTimeframeStrategy


class StrategyTask(Task):
    def __init__(self, symbol: Symbol, strategy: MultiTimeframeStrategy):
        super().__init__(
            f"StrategyTask({symbol.ccxt()}|{','.join(strategy.timeframes)})"
        )
        self.symbol: Symbol = symbol
        self.timeframes: list[str] = strategy.timeframes
        self.strategy: MultiTimeframeStrategy = strategy

    def run(self, kline: Kline) -> None:
        if (
            self.symbol.binance() == kline.symbol.binance()
            and kline.timeframe in self.timeframes
        ):
            self.strategy.run(kline)
