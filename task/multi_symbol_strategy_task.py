import json
import log
import re
from typing import Any, Dict, List
from data_event_loop import Task
from model import Symbol, Kline
from strategy import GeneralStrategy

logger = log.getLogger(__name__)

class MultiSymbolStrategyTask(Task):
    """
    支持多个交易对路由到同一个策略的Task
    """
    def __init__(self, symbols: List[Symbol], strategy: GeneralStrategy):
        super().__init__()
        self.name: str = 'MultiSymbolStrategyTask'
        self.symbols: List[Symbol] = symbols
        self.timeframes: List[str] = strategy.timeframes
        self.strategy: GeneralStrategy = strategy

        # 预先计算以便快速查找
        self.symbol_binances = {s.binance() for s in symbols}

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

            # 过滤逻辑：如果在允许的 symbol 集合内，且 timeframe 在策略关心的范围内
            if kline_obj.symbol.binance() in self.symbol_binances and kline_obj.timeframe in self.timeframes:
                self.strategy.run(kline_obj)
