import concurrent.futures
from typing import List
import logging

logger = logging.getLogger(__name__)

class Handler:
    def __init__(self):
        self.name: str

    def run(self, data: str):
        pass

class DataEventLoop:
    def __init__(self):
        self.handlers: List[Handler] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    def add_task(self, task: Handler):
        self.handlers.append(task)

    def loop(self, data: str):
        for handler in self.handlers:
            self.executor.submit(handler.run, data)

    def start(self):
        pass

    def stop(self):
        self.executor.shutdown(wait=False)
