import logging

from event_loop.base import Handler
from event_loop.event import Event, KlineEvent
from strategies import GeneralStrategy

logger = logging.getLogger(__name__)


class KlineHandler(Handler):
    def __init__(self, strategy: GeneralStrategy) -> None:
        super().__init__()
        self.strategy: GeneralStrategy = strategy
        self.timeframes = self.strategy.timeframes
        self.symbols = [s.ccxt() for s in self.strategy.symbols]

    def run(self, event: Event) -> None:
        if not isinstance(event, KlineEvent):
            return

        kline = event.kline
        if kline.symbol.ccxt() in self.symbols and kline.timeframe in self.timeframes:
            self.strategy.run(kline)
