import threading
import os
import log
from typing import List, Optional

from client.binance_client import BinanceSwapClient
from event_loop.binance import BinanceDataEventLoop
from event_loop.handler.kline_handler import KlineHandler
import dotenv

dotenv.load_dotenv()
logger = log.getLogger(__name__)

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


class BotManager:
    def __init__(self):
        self.main_binance_client: BinanceSwapClient = create_binance_client('main')
        self.data_event_loop: Optional[BinanceDataEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    def start_bot(self):
        from template import dogeusdc

        handlers: list[KlineHandler] = []

        doge_handler = dogeusdc.market_trend(self.main_binance_client)
        if doge_handler:
            handlers.append(doge_handler)

        kline_subscribes: List[str] = []
        self.data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

        for handler in handlers:
            for symbol in handler.strategy.symbols:
                for timeframe in handler.strategy.timeframes:
                    k = symbol.binance_ws_sub_kline(timeframe)
                    if k not in kline_subscribes:
                        kline_subscribes.append(k)

            self.data_event_loop.add_handler(handler)

        if len(kline_subscribes) == 0:
            logger.warning('No kline subscribes found')
            return

        logger.info("Starting BinanceDataEventLoop...")
        self.data_event_loop.start()

    def start_in_background(self):
        logger.info("Starting bot in background thread...")
        self._thread = threading.Thread(target=self.start_bot, daemon=True)
        self._thread.start()

    def stop(self):
        logger.info("Stopping BotManager...")
        if self.data_event_loop:
            self.data_event_loop.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BotManager stopped.")

bot_manager = BotManager()
