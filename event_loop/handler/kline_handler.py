import json
import log
import re
from typing import Any, Dict, List

from event_loop.base import Handler
from model import Symbol, Kline
from strategy import GeneralStrategy

logger = log.getLogger(__name__)

class StrategyHandler(Handler):
    def __init__(self, strategy: GeneralStrategy):
        super().__init__()
        self.name: str = 'StrategyHandler'
        self.strategy: GeneralStrategy = strategy
        self.timeframes = self.strategy.timeframes
        self.symbols = [s.ccxt() for s in self.strategy.symbols]

    def run(self, data: str) -> None:
        data_obj: Dict[str, Any] = json.loads(data)

        kline_key: str = data_obj.get('stream', '')
        is_kline: bool = '@kline_' in kline_key
        kline: Dict[str, Any] | None = data_obj.get('data', {}).get('k', None)

        if is_kline and kline:
            match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', kline_key)
            if not match:
                raise ValueError(f'Invalid kline key: {kline_key}')
            sym = Symbol(base=match.group(1), quote=match.group(2))

            k = Kline(
                symbol=sym,
                timeframe=match.group(3),
                open=float(kline['o']),
                high=float(kline['h']),
                low=float(kline['l']),
                close=float(kline['c']),
                volume=float(kline['v']),
                timestamp=int(kline['t']),
                finished=kline.get('x', False)
            )

            if sym.ccxt() in self.symbols and k.timeframe in self.timeframes:
                self.strategy.run(k)
