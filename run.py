import log
import os
from DataEventLoop import BinanceDataEventLoop
from client.binance_client import BinanceSwapClient
from model import Symbol
from template import long_short_rotation
from DataEventLoop import Task

logger = log.getLogger(__name__)

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
    task: Task = long_short_rotation.template(binance_client, symbol, timeframe)

    data_event_loop = BinanceDataEventLoop(kline_subscribes=[symbol.binance_ws_sub_kline(timeframe)])
    data_event_loop.add_task(task)

    data_event_loop.start()



