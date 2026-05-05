import os
from typing import Literal

import log
import dotenv
import uvicorn
import sys
from bot_manager import BotManager
from client.binance_client import BinanceSwapClient
from event_loop.binance import BinanceDataEventLoop

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def create_binance_client(client_type: Literal["MAIN", "COPY"]) -> BinanceSwapClient:
    api_key = os.environ.get(f'BINANCE_API_KEY_{client_type}')
    api_secret = os.environ.get(f'BINANCE_API_SECRET_{client_type}')
    is_test = os.environ.get(f'BINANCE_IS_TEST_{client_type}') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')

    logger.info('api_key: %s*****, api_secret: %s*****, is_test: %s',
                api_key[:5], api_secret[:5], is_test)
    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)
    return binance_client


def main():
    if "--no-api" in sys.argv:
        config_path = "strategies.yaml"
        for arg in sys.argv:
            if arg.startswith("--config="):
                config_path = arg.split("=", 1)[1]
        BotManager(
            ex_client=create_binance_client("MAIN"),
            el=BinanceDataEventLoop(kline_subscribes=[]),
            config_path=config_path,
        ).start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == '__main__':
    main()
