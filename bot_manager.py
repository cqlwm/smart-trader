import threading
import logging

from client.ex_client import ExSwapClient
from event_loop.base import DataEventLoop
from event_loop.binance import BinanceDataEventLoop
from event_loop.handler.kline_handler import KlineHandler
from strategy.instance_manager import StrategyInstanceManager
from strategy.loader import StrategyLoader
import strategy.strategies  # noqa: F401 — trigger auto-registration
import dotenv

dotenv.load_dotenv()
logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self, ex_client: ExSwapClient, el: DataEventLoop, config_path: str = "strategies.yaml") -> None:
        self.ex_client: ExSwapClient = ex_client
        self.data_event_loop: DataEventLoop = el
        self._config_path = config_path
        self._thread: threading.Thread | None = None
        self.instance_manager = StrategyInstanceManager()

    def start_bot(self) -> None:
        loader = StrategyLoader(self._config_path)
        handlers: list[KlineHandler] = loader.load(self.ex_client)

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
        thread = threading.Thread(target=self.start_bot, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        logger.info("Stopping BotManager...")
        if self.data_event_loop:
            self.data_event_loop.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BotManager stopped.")
