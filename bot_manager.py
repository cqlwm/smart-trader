import threading
import os
import logging
from typing import Literal

from client.binance_client import BinanceSwapClient
from event_loop.binance import BinanceDataEventLoop
from event_loop.handler.kline_handler import KlineHandler
from strategy.instance_manager import StrategyInstanceManager
import dotenv

dotenv.load_dotenv()
logger = logging.getLogger(__name__)


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


class BotManager:
    def __init__(self) -> None:
        self.main_binance_client: BinanceSwapClient | None = None
        self.data_event_loop: BinanceDataEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.instance_manager = StrategyInstanceManager()

    def _ensure_binance_client(self) -> BinanceSwapClient:
        if self.main_binance_client is None:
            self.main_binance_client = create_binance_client('MAIN')
        return self.main_binance_client

    def start_bot(self) -> None:
        from template import dogeusdc
        from template import btcusdc_smc

        client = self._ensure_binance_client()
        handlers: list[KlineHandler] = []

        doge_handler = dogeusdc.market_trend(client)
        if doge_handler:
            handlers.append(doge_handler)

        smc_handler = btcusdc_smc.smc_intraday(client)
        if smc_handler:
            handlers.append(smc_handler)

        kline_subscribes: list[str] = []
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

    def start_in_background(self) -> None:
        logger.info("Starting bot in background thread...")
        self._thread = threading.Thread(target=self.start_bot, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping BotManager...")
        if self.data_event_loop:
            self.data_event_loop.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BotManager stopped.")
