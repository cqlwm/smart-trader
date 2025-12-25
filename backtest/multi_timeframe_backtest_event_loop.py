import time
from typing import List, Dict, Any, Optional, Callable
import log

from data_event_loop import Task
from model import Kline
from backtest.backtest_client import BacktestClient
import json

logger = log.getLogger(__name__)


class MultiTimeframeBacktestEventLoop:
    """
    多时间框架回测事件循环，同时处理多个时间框架的历史数据重放（同步模式）
    """

    def __init__(self, historical_data: Dict[str, List[Kline]],
                 on_progress_callback: Optional[Callable[[int, int], None]] = None,
                 start_timestamp: Optional[int] = None,
                 start_index: Optional[int] = None):
        """
        初始化多时间框架回测事件循环

        Args:
            historical_data: 字典，key为timeframe，value为对应的历史K线数据列表
            on_progress_callback: 进度回调函数，参数为(当前索引, 总数)
            start_timestamp: 回测起始时间戳（优先使用）
            start_index: 回测起始索引（向后兼容用）
        """
        self.historical_data = historical_data
        self.timeframes = list(historical_data.keys())
        self.on_progress_callback = on_progress_callback

        # 任务列表
        self.tasks: List[Task] = []

        # 为每个时间框架设置起始索引
        self.start_indices = {}
        for timeframe in self.timeframes:
            klines = historical_data[timeframe]
            if start_timestamp is not None:
                # 根据时间戳找到对应的索引
                start_idx = 0
                for i, kline in enumerate(klines):
                    if kline.timestamp >= start_timestamp:
                        start_idx = i
                        break
                self.start_indices[timeframe] = max(0, min(start_idx, len(klines) - 1))
            elif start_index is not None:
                # 使用索引（向后兼容）
                self.start_indices[timeframe] = max(0, min(start_index, len(klines) - 1))
            else:
                # 默认值
                self.start_indices[timeframe] = 300

        # 收集所有K线并按时间排序
        self.sorted_klines = self._collect_and_sort_klines()
        self.current_kline_index = 0

        self.is_running = False
        self.is_paused = False
        self.backtest_client: Optional[BacktestClient] = None

        logger.info(f"MultiTimeframeBacktestEventLoop initialized with timeframes: {self.timeframes}, total sorted klines: {len(self.sorted_klines)}")

    def set_backtest_client(self, client: BacktestClient):
        """设置回测客户端"""
        self.backtest_client = client

    def add_task(self, task: Task):
        """添加任务"""
        self.tasks.append(task)

    def loop(self, data: str):
        """同步执行所有任务"""
        for task in self.tasks:
            task.run(data)

    def start(self):
        """开始回测（同步执行）"""
        if self.is_running:
            logger.warning("Backtest already running")
            return

        if not self.sorted_klines:
            logger.error("No sorted klines available")
            return

        self.is_running = True
        self.is_paused = False
        self.current_kline_index = 0

        logger.info("Multi-timeframe backtest started (synchronous mode)")

        # 同步执行回测
        self._run_backtest_sync()

    def stop(self):
        """停止回测"""
        self.is_running = False
        logger.info("Multi-timeframe backtest stopped")

    def pause(self):
        """暂停回测（在同步模式下无效）"""
        logger.warning("Pause not supported in synchronous mode")

    def resume(self):
        """恢复回测（在同步模式下无效）"""
        logger.warning("Resume not supported in synchronous mode")

    def step(self):
        """单步执行（在同步模式下无效）"""
        logger.warning("Step not supported in synchronous mode")

    def seek_to_index(self, index: int):
        """跳转到指定索引"""
        if 0 <= index < len(self.sorted_klines):
            self.current_kline_index = index
            logger.info(f"Seeked to kline index {index}")
        else:
            logger.warning(f"Invalid index {index}, total klines: {len(self.sorted_klines)}")

    def _collect_and_sort_klines(self) -> List[Kline]:
        """收集所有时间框架的K线并按时间戳排序"""
        all_klines = []

        for timeframe in self.timeframes:
            klines = self.historical_data[timeframe]
            start_idx = self.start_indices[timeframe]

            # 只包含从start_index开始的K线，并且只处理完成的K线
            for i in range(start_idx, len(klines)):
                kline = klines[i]
                if kline.finished:
                    all_klines.append(kline)

        # 按时间戳排序
        all_klines.sort(key=lambda k: k.timestamp)

        logger.info(f"Collected and sorted {len(all_klines)} klines from {len(self.timeframes)} timeframes")
        return all_klines

    def _run_backtest_sync(self):
        """同步运行回测的主循环"""
        while self.is_running and self.current_kline_index < len(self.sorted_klines):
            # 处理当前K线
            current_kline = self.sorted_klines[self.current_kline_index]

            # 更新回测客户端的价格和时间戳
            if self.backtest_client:
                self.backtest_client.update_current_price(current_kline.symbol, current_kline.close)
                self.backtest_client.update_current_timestamp(current_kline.timestamp)

            # 构造WebSocket消息并同步执行所有任务
            message_data = self._kline_to_ws_message(current_kline)
            self.loop(message_data)

            # 进度回调
            if self.on_progress_callback:
                self.on_progress_callback(self.current_kline_index + 1, len(self.sorted_klines))

            self.current_kline_index += 1

        self.is_running = False
        logger.info("Multi-timeframe backtest completed (synchronous mode)")

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
        """获取回测进度（0.0-1.0）"""
        if not self.sorted_klines:
            return 0.0

        return min(1.0, max(0.0, self.current_kline_index / len(self.sorted_klines)))

    @property
    def current_kline(self) -> Optional[Kline]:
        """获取当前K线"""
        if 0 <= self.current_kline_index - 1 < len(self.sorted_klines):
            return self.sorted_klines[self.current_kline_index - 1]
        return None

    @property
    def is_completed(self) -> bool:
        """检查回测是否完成"""
        return self.current_kline_index >= len(self.sorted_klines)
