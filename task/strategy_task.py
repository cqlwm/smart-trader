import json
import log
import re
from typing import Any, Dict
from data_event_loop import Task
from model import Symbol, Kline
from strategy import StrategyV2

logger = log.getLogger(__name__)

class StrategyTask(Task):
    def __init__(self, symbol: Symbol, timeframe: str, strategy: StrategyV2):
        super().__init__()
        self.name = 'StrategyTask'
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy

    def run(self, data: str) -> None:
        data_obj: Dict[str, Any] = json.loads(data)

        kline_key: str = data_obj.get('stream', '')
        is_kline: bool = '@kline_' in kline_key
        kline: Dict[str, Any] | None = data_obj.get('data', {}).get('k', None)
        
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
            if self.symbol.binance() == kline_obj.symbol.binance() and self.timeframe == kline_obj.timeframe:
                self.strategy.run(kline_obj)
