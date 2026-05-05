import concurrent.futures
import logging

from model import Symbol
from event_loop.event import Event

logger = logging.getLogger(__name__)


class Handler:

    def run(self, event: Event) -> None:
        pass


class DataEventLoop:
    def __init__(self) -> None:
        self.handlers: list[Handler] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    def add_handler(self, handler: Handler) -> None:
        self.handlers.append(handler)

    def subscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        pass

    def unsubscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        pass

    def loop(self, event: Event) -> None:
        for handler in self.handlers:
            self.executor.submit(handler.run, event)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.executor.shutdown(wait=False)
