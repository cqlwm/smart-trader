import time
from typing import List, Dict, Any, Optional, Callable
import threading
import log

from data_event_loop import DataEventLoop, Task
from model import Kline, Symbol
from backtest.data_loader import HistoricalDataLoader
from backtest.backtest_client import BacktestClient
import json

logger = log.getLogger(__name__)


class BacktestEventLoop(DataEventLoop):
    """
    回测事件循环，从历史数据重放K线
    """

    def __init__(self, historical_klines: List[Kline],
                 on_progress_callback: Optional[Callable[[int, int], None]] = None,
                 start_index: int = 300):
        """
        初始化回测事件循环

        Args:
            historical_klines: 历史K线数据列表（已按时间排序）
            on_progress_callback: 进度回调函数，参数为(当前索引, 总数)
            start_index: 回测起始索引，默认300（为MultiTimeframeStrategy预留初始化数据）
        """
        super().__init__()
        self.historical_klines = historical_klines
        self.on_progress_callback = on_progress_callback

        # 设置起始索引，确保在有效范围内
        self.start_index = max(0, min(start_index, len(historical_klines) - 1))
        self.current_index = self.start_index

        self.is_running = False
        self.is_paused = False
        self.backtest_client: Optional[BacktestClient] = None

        # 控制线程
        self.control_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

        logger.info(f"BacktestEventLoop initialized with {len(historical_klines)} klines, start_index: {self.start_index}")

    def set_backtest_client(self, client: BacktestClient):
        """设置回测客户端"""
        self.backtest_client = client

    def start(self):
        """开始回测"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        if not self.historical_klines:
            logger.error("No historical data available")
            return

        self.is_running = True
        self.is_paused = False
        self.current_index = self.start_index  # 从start_index开始回测
        self.stop_event.clear()

        self.control_thread = threading.Thread(target=self._run_backtest)
        self.control_thread.daemon = True
        self.control_thread.start()

        logger.info(f"Backtest started from index {self.start_index}")

    def stop(self):
        """停止回测"""
        if not self.is_running:
            return

        self.is_running = False
        self.stop_event.set()

        if self.control_thread and self.control_thread.is_alive():
            self.control_thread.join(timeout=5.0)

        super().stop()
        logger.info("Backtest stopped")

    def pause(self):
        """暂停回测"""
        self.is_paused = True
        logger.info("Backtest paused")

    def resume(self):
        """恢复回测"""
        self.is_paused = False
        logger.info("Backtest resumed")

    def step(self):
        """单步执行（仅在暂停状态下有效）"""
        if not self.is_paused or not self.is_running:
            return

        self._process_next_kline()

    def seek_to_index(self, index: int):
        """跳转到指定索引"""
        if 0 <= index < len(self.historical_klines):
            self.current_index = index
            logger.info(f"Seeked to index {index}")

    def seek_to_timestamp(self, timestamp: int):
        """跳转到指定时间戳"""
        for i, kline in enumerate(self.historical_klines):
            if kline.timestamp >= timestamp:
                self.current_index = i
                logger.info(f"Seeked to timestamp {timestamp} (index {i})")
                break

    def _run_backtest(self):
        """运行回测的主循环"""
        while self.is_running and self.current_index < len(self.historical_klines):
            if self.stop_event.is_set():
                break

            if self.is_paused:
                time.sleep(0.1)  # 暂停时短暂休眠
                continue

            # 处理下一根K线
            self._process_next_kline()

            # 进度回调
            if self.on_progress_callback:
                self.on_progress_callback(self.current_index, len(self.historical_klines))

        self.is_running = False
        logger.info("Backtest completed")

    def _process_next_kline(self):
        """处理下一根K线"""
        if self.current_index >= len(self.historical_klines):
            return

        kline = self.historical_klines[self.current_index]

        # 更新回测客户端的价格和时间戳
        if self.backtest_client:
            self.backtest_client.update_current_price(kline.symbol, kline.close)
            self.backtest_client.update_current_timestamp(kline.timestamp)

        # 构造WebSocket消息格式的数据
        message_data = self._kline_to_ws_message(kline)

        # 分发给所有任务
        self.loop(message_data)

        self.current_index += 1

    def _kline_to_ws_message(self, kline: Kline) -> str:
        """
        将Kline对象转换为WebSocket消息格式
        模拟Binance WebSocket的数据格式
        """
        # 构造Binance WebSocket格式的数据
        ws_data = {
            "stream": kline.symbol.binance_ws_sub_kline(kline.timeframe),
            "data": {
                "e": "kline",
                "E": kline.timestamp,
                "s": kline.symbol.binance(),
                "k": {
                    "t": kline.timestamp,  # Kline start time
                    "T": kline.timestamp + self._get_timeframe_ms(kline.timeframe),  # Kline close time
                    "s": kline.symbol.binance(),  # Symbol
                    "i": kline.timeframe,  # Interval
                    "f": 100,  # First trade ID (模拟)
                    "L": 200,  # Last trade ID (模拟)
                    "o": str(kline.open),  # Open price
                    "c": str(kline.close),  # Close price
                    "h": str(kline.high),  # High price
                    "l": str(kline.low),  # Low price
                    "v": str(kline.volume),  # Base asset volume
                    "n": 100,  # Number of trades (模拟)
                    "x": kline.finished,  # Is this kline closed?
                    "q": str(kline.volume * kline.close),  # Quote asset volume
                    "V": str(kline.volume),  # Taker buy base asset volume
                    "Q": str(kline.volume * kline.close),  # Taker buy quote asset volume
                    "B": "0"  # Ignore
                }
            }
        }

        return json.dumps(ws_data)

    def _get_timeframe_ms(self, timeframe: str) -> int:
        """将时间框架转换为毫秒"""
        # 解析时间框架，如"1m", "5m", "1h", "1d"
        if timeframe.endswith('m'):
            return int(timeframe[:-1]) * 60 * 1000
        elif timeframe.endswith('h'):
            return int(timeframe[:-1]) * 60 * 60 * 1000
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 24 * 60 * 60 * 1000
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 7 * 24 * 60 * 60 * 1000
        else:
            # 默认1分钟
            return 60 * 1000

    @property
    def progress(self) -> float:
        """获取回测进度（0.0-1.0），相对于start_index计算"""
        if not self.historical_klines:
            return 0.0

        # 计算实际回测范围（从start_index到结束）
        total_backtest_klines = len(self.historical_klines) - self.start_index
        if total_backtest_klines <= 0:
            return 1.0

        # 计算当前在回测范围内的进度
        current_backtest_index = self.current_index - self.start_index
        return min(1.0, max(0.0, current_backtest_index / total_backtest_klines))

    @property
    def current_kline(self) -> Optional[Kline]:
        """获取当前K线"""
        if 0 <= self.current_index - 1 < len(self.historical_klines):
            return self.historical_klines[self.current_index - 1]
        return None

    @property
    def is_completed(self) -> bool:
        """检查回测是否完成"""
        return self.current_index >= len(self.historical_klines)
