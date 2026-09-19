import json
import re

from smart_trader import log
from smart_trader.data_event_loop import Task
from smart_trader.model import Kline, Symbol
from smart_trader.strategy import MultiTimeframeStrategy

logger = log.getLogger(__name__)

class StrategyTask(Task):
    def __init__(self, symbol: Symbol, strategy: MultiTimeframeStrategy):
        super().__init__()
        self.name: str = 'StrategyTask'
        self.symbol: Symbol = symbol
        self.timeframes: list[str] = strategy.timeframes
        self.strategy: MultiTimeframeStrategy = strategy

    def run(self, data: str) -> None:
        data_obj: dict[str, any] = json.loads(data)

        kline_key: str = data_obj.get('stream', '')
        is_kline: bool = '@kline_' in kline_key
        kline: dict[str, any] | None = data_obj.get('data', {}).get('k', None)

        if is_kline and kline:
            match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', kline_key)
            if not match:
                raise ValueError(f'Invalid kline key: {kline_key}')
            kline_obj = Kline(
                symbol=Symbol(base=match.group(1), quote=match.group(2)),
                timeframe=match.group(3),
                open=float(kline['o']),
                high=float(kline['h']),
                low=float(kline['l']),
                close=float(kline['c']),
                volume=float(kline['v']),
                timestamp=int(kline['t']),
                finished=kline.get('x', False)
            )
            if self.symbol.binance() == kline_obj.symbol.binance() and kline_obj.timeframe in self.timeframes:
                self.strategy.run(kline_obj)
