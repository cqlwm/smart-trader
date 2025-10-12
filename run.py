from typing import List
import log
import os
from data_event_loop import BinanceDataEventLoop
from client.binance_client import BinanceSwapClient
import dotenv

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def dev_debug():
    node_env = os.environ.get('NODE_ENV', 'NONE')
    logger.info(f'NODE_ENV: {node_env}')
    if node_env != 'DEV':
        return

if __name__ == '__main__':
    api_key = os.environ.get('BINANCE_API_KEY')
    api_secret = os.environ.get('BINANCE_API_SECRET')
    is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
    else:
        logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)

    from template import long_buy_bnb
    tasks = [
        long_buy_bnb.template(binance_client)
    ]

    kline_subscribes: List[str] = []
    data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

    for task in tasks:
        kline_subscribes.append(task.symbol.binance_ws_sub_kline(task.timeframe))
        data_event_loop.add_task(task)

    data_event_loop.start()



