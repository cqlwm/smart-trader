from typing import List, Dict, Any, Optional, Callable
import log

from event_loop.base import DataEventLoop, Handler
from model import Kline, Symbol
from backtest.backtest_client import BacktestClient
import json

logger = log.getLogger(__name__)


class BacktestEventLoop(DataEventLoop):
    """
    回测事件循环，从历史数据重放K线（同步模式）
    """

    def __init__(self, historical_klines: List[Kline],
                 on_progress_callback: Optional[Callable[[int, int], None]] = None,
                 start_timestamp: Optional[int] = None,
                 start_index: Optional[int] = None):
        """
        初始化回测事件循环

        Args:
            historical_klines: 历史K线数据列表（已按时间排序）
            on_progress_callback: 进度回调函数，参数为(当前索引, 总数)
            start_timestamp: 回测起始时间戳（优先使用）
            start_index: 回测起始索引（向后兼容，默认300条预热数据）
        """
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
            # 默认跳过前300根作为策略预热数据
            self.start_index = 300

        self.current_index = self.start_index
        self.is_running = False
        self.backtest_client: Optional[BacktestClient] = None

        logger.info(f"BacktestEventLoop initialized with {len(historical_klines)} klines, start_index: {self.start_index}")

    def set_backtest_client(self, client: BacktestClient):
        self.backtest_client = client

    def loop(self, data: str):
        """同步执行所有任务，保证时序确定性"""
        for task in self.handlers:
            task.run(data)

    def start(self):
        """开始回测（同步执行，阻塞直到完成）"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        if not self.historical_klines:
            logger.error("No historical data available")
            return

        self.is_running = True
        self.current_index = self.start_index

        logger.info(f"Backtest started from index {self.start_index}")
        self._run_backtest_sync()

    def stop(self):
        """停止回测"""
        self.is_running = False
        super().stop()
        logger.info("Backtest stopped")

    def _run_backtest_sync(self):
        """同步运行回测的主循环"""
        while self.is_running and self.current_index < len(self.historical_klines):
            self._process_next_kline()

            if self.on_progress_callback:
                self.on_progress_callback(self.current_index, len(self.historical_klines))

        self.is_running = False
        logger.info("Backtest completed")

    def _process_next_kline(self):
        if self.current_index >= len(self.historical_klines):
            return

        kline = self.historical_klines[self.current_index]

        if self.backtest_client:
            self.backtest_client.update_current_price(kline.symbol, kline.close)
            self.backtest_client.update_current_timestamp(kline.timestamp)

        message_data = self._kline_to_ws_message(kline)
        self.loop(message_data)

        # 策略执行完后检查限价挂单是否触及成交
        if self.backtest_client:
            self.backtest_client.check_pending_orders(kline)

        self.current_index += 1

    def _kline_to_ws_message(self, kline: Kline) -> str:
        ws_data = {
            "stream": kline.symbol.binance_ws_sub_kline(kline.timeframe),
            "data": {
                "e": "kline",
                "E": kline.timestamp,
                "s": kline.symbol.binance(),
                "k": {
                    "t": kline.timestamp,
                    "T": kline.timestamp + self._get_timeframe_ms(kline.timeframe),
                    "s": kline.symbol.binance(),
                    "i": kline.timeframe,
                    "f": 100,
                    "L": 200,
                    "o": str(kline.open),
                    "c": str(kline.close),
                    "h": str(kline.high),
                    "l": str(kline.low),
                    "v": str(kline.volume),
                    "n": 100,
                    "x": kline.finished,
                    "q": str(kline.volume * kline.close),
                    "V": str(kline.volume),
                    "Q": str(kline.volume * kline.close),
                    "B": "0"
                }
            }
        }
        return json.dumps(ws_data)

    def _get_timeframe_ms(self, timeframe: str) -> int:
        if timeframe.endswith('m'):
            return int(timeframe[:-1]) * 60 * 1000
        elif timeframe.endswith('h'):
            return int(timeframe[:-1]) * 60 * 60 * 1000
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 24 * 60 * 60 * 1000
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 7 * 24 * 60 * 60 * 1000
        return 60 * 1000

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
    def current_kline(self) -> Optional[Kline]:
        if 0 <= self.current_index - 1 < len(self.historical_klines):
            return self.historical_klines[self.current_index - 1]
        return None

    @property
    def is_completed(self) -> bool:
        return self.current_index >= len(self.historical_klines)
