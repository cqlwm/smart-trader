import time
from typing import List, Dict, Any, Optional, Callable
import threading
import log
from collections import defaultdict

from data_event_loop import DataEventLoop, Task
from model import Kline, Symbol
from backtest.data_loader import HistoricalDataLoader
from backtest.backtest_client import BacktestClient
import json

logger = log.getLogger(__name__)


class MultiTimeframeBacktestEventLoop(DataEventLoop):
    """
    多时间框架回测事件循环，同时处理多个时间框架的历史数据重放
    """

    def __init__(self, historical_data: Dict[str, List[Kline]], speed_multiplier: float = 1.0,
                 on_progress_callback: Optional[Callable[[int, int], None]] = None,
                 start_index: int = 300):
        """
        初始化多时间框架回测事件循环

        Args:
            historical_data: 字典，key为timeframe，value为对应的历史K线数据列表
            speed_multiplier: 回放速度倍数，1.0为实时，0为手动步进
            on_progress_callback: 进度回调函数，参数为(当前索引, 总数)
            start_index: 回测起始索引，默认300（为MultiTimeframeStrategy预留初始化数据）
        """
        super().__init__()
        self.historical_data = historical_data
        self.timeframes = list(historical_data.keys())
        self.speed_multiplier = speed_multiplier
        self.on_progress_callback = on_progress_callback

        # 为每个时间框架设置起始索引
        self.start_indices = {}
        for timeframe in self.timeframes:
            klines = historical_data[timeframe]
            self.start_indices[timeframe] = max(0, min(start_index, len(klines) - 1))

        self.current_indices = self.start_indices.copy()

        # 找到最长的K线序列作为主时间线
        self.main_timeframe = max(self.timeframes, key=lambda tf: len(historical_data[tf]))
        self.main_klines = historical_data[self.main_timeframe]
        self.main_start_index = self.start_indices[self.main_timeframe]
        self.main_current_index = self.main_start_index

        self.is_running = False
        self.is_paused = False
        self.backtest_client: Optional[BacktestClient] = None

        # 控制线程
        self.control_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

        logger.info(f"MultiTimeframeBacktestEventLoop initialized with timeframes: {self.timeframes}, main: {self.main_timeframe}, start_indices: {self.start_indices}, speed: {speed_multiplier}x")

    def set_backtest_client(self, client: BacktestClient):
        """设置回测客户端"""
        self.backtest_client = client

    def start(self):
        """开始回测"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        if not self.historical_data:
            logger.error("No historical data available")
            return

        self.is_running = True
        self.is_paused = False
        self.main_current_index = self.main_start_index  # 从start_index开始回测
        self.stop_event.clear()

        self.control_thread = threading.Thread(target=self._run_backtest)
        self.control_thread.daemon = True
        self.control_thread.start()

        logger.info(f"Multi-timeframe backtest started from index {self.main_start_index}")

    def stop(self):
        """停止回测"""
        if not self.is_running:
            return

        self.is_running = False
        self.stop_event.set()

        if self.control_thread and self.control_thread.is_alive():
            self.control_thread.join(timeout=5.0)

        super().stop()
        logger.info("Multi-timeframe backtest stopped")

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

        self._process_next_kline_group()

    def seek_to_index(self, index: int):
        """跳转到指定索引"""
        for timeframe in self.timeframes:
            klines = self.historical_data[timeframe]
            if 0 <= index < len(klines):
                self.current_indices[timeframe] = index

        # 更新主时间线索引
        if 0 <= index < len(self.main_klines):
            self.main_current_index = index

        logger.info(f"Seeked to index {index}")

    def _run_backtest(self):
        """运行回测的主循环"""
        last_timestamp = 0

        while self.is_running and self.main_current_index < len(self.main_klines):
            if self.stop_event.is_set():
                break

            if self.is_paused:
                time.sleep(0.1)  # 暂停时短暂休眠
                continue

            # 处理下一组K线
            self._process_next_kline_group()

            # 计算等待时间（基于速度倍数）
            if self.speed_multiplier > 0 and self.main_current_index > 0:
                current_kline = self.main_klines[self.main_current_index - 1]
                time_diff = current_kline.timestamp - last_timestamp

                if time_diff > 0:
                    wait_time = (time_diff / 1000.0) / self.speed_multiplier
                    if wait_time > 0:
                        time.sleep(wait_time)

                last_timestamp = current_kline.timestamp

            # 进度回调
            if self.on_progress_callback:
                self.on_progress_callback(self.main_current_index, len(self.main_klines))

        self.is_running = False
        logger.info("Multi-timeframe backtest completed")

    def _process_next_kline_group(self):
        """处理下一组K线（所有时间框架的当前K线）"""
        if self.main_current_index >= len(self.main_klines):
            return

        # 获取主时间线的当前K线
        main_kline = self.main_klines[self.main_current_index]
        current_timestamp = main_kline.timestamp

        # 找到所有时间框架中在这个时间戳或之前的最新K线
        klines_to_process = []

        for timeframe in self.timeframes:
            klines = self.historical_data[timeframe]
            current_index = self.current_indices[timeframe]

            # 找到时间戳小于等于当前时间戳的最新K线
            while current_index < len(klines) and klines[current_index].timestamp <= current_timestamp:
                kline = klines[current_index]
                if kline.finished:  # 只处理完成的K线
                    klines_to_process.append(kline)
                current_index += 1

            self.current_indices[timeframe] = current_index

        # 更新回测客户端的价格和时间戳（使用主时间线的价格）
        if self.backtest_client:
            self.backtest_client.update_current_price(main_kline.symbol, main_kline.close)
            self.backtest_client.update_current_timestamp(current_timestamp)

        # 为每个K线构造WebSocket消息并分发
        for kline in klines_to_process:
            message_data = self._kline_to_ws_message(kline)
            self.loop(message_data)

        self.main_current_index += 1

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
        else:
            # 默认1分钟
            return 60 * 1000

    @property
    def progress(self) -> float:
        """获取回测进度（0.0-1.0），相对于start_index计算"""
        if not self.main_klines:
            return 0.0

        # 计算实际回测范围（从start_index到结束）
        total_backtest_klines = len(self.main_klines) - self.main_start_index
        if total_backtest_klines <= 0:
            return 1.0

        # 计算当前在回测范围内的进度
        current_backtest_index = self.main_current_index - self.main_start_index
        return min(1.0, max(0.0, current_backtest_index / total_backtest_klines))

    @property
    def current_kline(self) -> Optional[Kline]:
        """获取当前主时间线K线"""
        if 0 <= self.main_current_index - 1 < len(self.main_klines):
            return self.main_klines[self.main_current_index - 1]
        return None

    @property
    def is_completed(self) -> bool:
        """检查回测是否完成"""
        return self.main_current_index >= len(self.main_klines)
