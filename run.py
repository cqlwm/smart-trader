from typing import List
import log
import os
from data_event_loop import BinanceDataEventLoop
from client.binance_client import BinanceSwapClient
import dotenv

from template import zecusdc

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def dev_debug():
    node_env = os.environ.get('NODE_ENV', 'NONE')
    logger.info(f'NODE_ENV: {node_env}')
    if node_env != 'DEV':
        return

def create_binance_client(client_type: str) -> BinanceSwapClient:
    api_key = os.environ.get(f'BINANCE_API_KEY_{client_type.upper()}')
    api_secret = os.environ.get(f'BINANCE_API_SECRET_{client_type.upper()}')
    is_test = os.environ.get(f'BINANCE_IS_TEST_{client_type.upper()}') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
    else:
        logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)
    return binance_client

# copy-trading binance client
copy_trading_binance_client: BinanceSwapClient = create_binance_client('copy')

# main binance client
main_binance_client: BinanceSwapClient = create_binance_client('main')

if __name__ == '__main__':

    from template import dogeusdc, btcdom, zecusdc, btcusdc
    
    tasks = [
        # bobusdt.long_buy(main_binance_client),

        btcdom.long_buy_simple_grid(copy_trading_binance_client),

        zecusdc.short_sell(main_binance_client),

        dogeusdc.long_buy(main_binance_client),
        dogeusdc.short_sell(main_binance_client),

        btcusdc.short_sell(main_binance_client),
    ]

    kline_subscribes: List[str] = []
    data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

    for task in tasks:
        sub_key = task.symbol.binance_ws_sub_kline(task.timeframe)
        if sub_key not in kline_subscribes:
            kline_subscribes.append(sub_key)
        data_event_loop.add_task(task)

    data_event_loop.start()



