import json
import log
import os
import re
from typing import Any, Dict
from DataEventLoop import BinanceDataEventLoop, Task
from client.binance_client import BinanceSwapClient
from model import Symbol, Kline
from strategy import StrategyV2
from template import long_short_rotation

logger = log.getLogger(__name__)

class StrategyTask(Task):
    def __init__(self, strategy: StrategyV2):
        super().__init__()
        self.name = 'StrategyTask'
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
            self.strategy.run(kline_obj)

if __name__ == '__main__':
    api_key = os.environ.get('BINANCE_API_KEY')
    api_secret = os.environ.get('BINANCE_API_SECRET')
    is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
    else:
        logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)

    timeframe = '1m'
    symbol = Symbol(base='bnb', quote='usdc')
    task = long_short_rotation.template(binance_client, symbol, timeframe)

    data_event_loop = BinanceDataEventLoop(kline_subscribes=[symbol.binance_ws_sub_kline(timeframe)])
    data_event_loop.add_task(task)

    data_event_loop.start()



