from typing import List
import log
import os
from client.binance_client import BinanceSwapClient
import dotenv

from event_loop.binance import BinanceDataEventLoop
from event_loop.handler.kline_handler import KlineHandler

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
# copy_trading_binance_client: BinanceSwapClient = create_binance_client('copy')

# main binance client
main_binance_client: BinanceSwapClient = create_binance_client('main')

import uvicorn
import sys

def main():
    if "--no-api" in sys.argv:
        from bot_manager import bot_manager
        bot_manager.start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == '__main__':
    main()
