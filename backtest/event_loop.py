import logging
from datetime import datetime, timezone

from backtest.types import BacktestConfig
from event_loop.base import DataEventLoop
from event_loop.event import KlineEvent
from model import Kline, Symbol
from backtest.client import BacktestClient

logger = logging.getLogger(__name__)


def _parse_date_to_timestamp(date_str: str) -> int:
    """将 YYYY-MM-DD 格式转为 UTC 毫秒时间戳"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class BacktestEventLoop(DataEventLoop):
    """回测事件循环，从历史数据重放K线（同步模式）"""

    def __init__(self, config: BacktestConfig, backtest_client: BacktestClient) -> None:
        super().__init__()
        self._config = config
        self._start_ts = _parse_date_to_timestamp(config.start_date)
        self._end_ts = _parse_date_to_timestamp(config.end_date)
        self._subscriptions: dict[str, tuple[Symbol, str]] = {}
        self.start_index = 0
        self.end_index = 0
        self.current_index = 0
        self.is_running = False
        self.backtest_client: BacktestClient = backtest_client
        self.historical_klines: list[Kline] = []
        self.default_warmup = 300

    def subscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        for symbol in symbols:
            for tf in timeframes:
                self._subscriptions[f"{symbol.simple()}_{tf}"] = (symbol, tf)

    def loop(self, event: KlineEvent) -> None:  # type: ignore[override]
        """同步执行所有任务，保证时序确定性"""
        for handler in self.handlers:
            handler.run(event)

    def start(self) -> None:
        """开始回测（同步执行，阻塞直到完成）"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        self.historical_klines = self._load_subscribed_klines()

        if not self.historical_klines:
            logger.warning("No historical data available")
            return

        self._resolve_start_index()
        self._resolve_end_index()

        self.is_running = True
        self.current_index = self.start_index

        logger.info("Backtest started from index %d to %d (%d klines loaded)",
                    self.start_index, self.end_index, len(self.historical_klines))
        self._run_backtest_sync()

    def stop(self) -> None:
        self.is_running = False
        super().stop()
        logger.info("Backtest stopped")

    def _load_subscribed_klines(self) -> list[Kline]:
        collected: list[Kline] = []

        for _, (symbol, tf) in self._subscriptions.items():
            klines = self.backtest_client.fetch_ohlcv(
                symbol, tf,
                start_time=self._start_ts,
                end_time=self._end_ts,
                limit=0,
            )
            collected.extend(klines)

        return sorted(collected, key=lambda k: k.timestamp)

    def _resolve_start_index(self) -> None:
        default_warmup = min(self.default_warmup, len(self.historical_klines) - 1)
        for i, kline in enumerate(self.historical_klines):
            if kline.timestamp >= self._start_ts:
                self.start_index = i
                return
        self.start_index = default_warmup

    def _resolve_end_index(self) -> None:
        for i, kline in enumerate(self.historical_klines):
            if kline.timestamp >= self._end_ts:
                self.end_index = i
                return
        self.end_index = len(self.historical_klines)

    def _run_backtest_sync(self) -> None:
        while self.is_running and self.current_index < self.end_index:
            self._process_next_kline()

        self.is_running = False
        logger.info("Backtest completed")

    def _process_next_kline(self) -> None:
        if self.current_index >= len(self.historical_klines):
            return

        kline = self.historical_klines[self.current_index]

        self.backtest_client.update_current_price(kline.symbol, kline.close)
        self.backtest_client.update_current_timestamp(kline.timestamp)

        kline_event = KlineEvent(timestamp=kline.timestamp, kline=kline)
        self.loop(kline_event)

        self.backtest_client.check_pending_orders(kline)

        self.current_index += 1

    @property
    def progress(self) -> float:
        if not self.historical_klines:
            return 0.0
        total_backtest_klines = self.end_index - self.start_index
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
        return self.current_index >= self.end_index
