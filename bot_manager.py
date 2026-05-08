import threading
import logging

from client.ex_client import ExSwapClient
from event_loop.base import DataEventLoop
from event_loop.handler.kline_handler import KlineHandler
from strategies.instance_manager import StrategyInstanceManager
from strategies.loader import StrategyLoader
import strategies  # noqa: F401 — trigger auto-registration
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

        for handler in handlers:
            self.data_event_loop.add_handler(handler)
            self.data_event_loop.subscribe(handler.strategy.symbols, handler.strategy.timeframes)

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
