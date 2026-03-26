import logging
from typing import Callable

from event_loop.base import DataEventLoop
from event_loop.event import KlineEvent
from model import Kline
from backtest.backtest_client import BacktestClient

logger = logging.getLogger(__name__)


class BacktestEventLoop(DataEventLoop):
    """回测事件循环，从历史数据重放K线（同步模式）"""

    def __init__(self, historical_klines: list[Kline],
                 on_progress_callback: Callable[[int, int], None] | None = None,
                 start_timestamp: int | None = None,
                 start_index: int | None = None) -> None:
        super().__init__()
        self.historical_klines = historical_klines
        self.on_progress_callback = on_progress_callback

        if start_timestamp is not None:
            self.start_index = 0
            for i, kline in enumerate(historical_klines):
                if kline.timestamp >= start_timestamp:
                    self.start_index = i
                    break
            self.start_index = max(0, min(self.start_index, len(historical_klines) - 1))
        elif start_index is not None:
            self.start_index = max(0, min(start_index, len(historical_klines) - 1))
        else:
            self.start_index = 300

        self.current_index = self.start_index
        self.is_running = False
        self.backtest_client: BacktestClient | None = None

        logger.info("BacktestEventLoop initialized with %d klines, start_index: %d",
                     len(historical_klines), self.start_index)

    def set_backtest_client(self, client: BacktestClient) -> None:
        self.backtest_client = client

    def loop(self, event: KlineEvent) -> None:  # type: ignore[override]
        """同步执行所有任务，保证时序确定性"""
        for handler in self.handlers:
            handler.run(event)

    def start(self) -> None:
        """开始回测（同步执行，阻塞直到完成）"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        if not self.historical_klines:
            logger.warning("No historical data available")
            return

        self.is_running = True
        self.current_index = self.start_index

        logger.info("Backtest started from index %d", self.start_index)
        self._run_backtest_sync()

    def stop(self) -> None:
        self.is_running = False
        super().stop()
        logger.info("Backtest stopped")

    def _run_backtest_sync(self) -> None:
        while self.is_running and self.current_index < len(self.historical_klines):
            self._process_next_kline()

            if self.on_progress_callback:
                self.on_progress_callback(self.current_index, len(self.historical_klines))

        self.is_running = False
        logger.info("Backtest completed")

    def _process_next_kline(self) -> None:
        if self.current_index >= len(self.historical_klines):
            return

        kline = self.historical_klines[self.current_index]

        if self.backtest_client:
            self.backtest_client.update_current_price(kline.symbol, kline.close)
            self.backtest_client.update_current_timestamp(kline.timestamp)

        kline_event = KlineEvent(timestamp=kline.timestamp, kline=kline)
        self.loop(kline_event)

        if self.backtest_client:
            self.backtest_client.check_pending_orders(kline)

        self.current_index += 1

    @property
    def progress(self) -> float:
        if not self.historical_klines:
            return 0.0
        total_backtest_klines = len(self.historical_klines) - self.start_index
        if total_backtest_klines <= 0:
            return 1.0
        current_backtest_index = self.current_index - self.start_index
        return min(1.0, max(0.0, current_backtest_index / total_backtest_klines))

    @property
    def current_kline(self) -> Kline | None:
        if 0 <= self.current_index - 1 < len(self.historical_klines):
            return self.historical_klines[self.current_index - 1]
        return None

    @property
    def is_completed(self) -> bool:
        return self.current_index >= len(self.historical_klines)
