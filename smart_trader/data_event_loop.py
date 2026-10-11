import concurrent.futures
import json
import math
import random
import re
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, cast

import websocket

from smart_trader import log
from smart_trader.client.ports import MarketDataPort
from smart_trader.model import Kline, Symbol, timeframe_seconds

logger = log.getLogger(__name__)

_KLINE_STREAM_PATTERN = re.compile(r"(\w+?)(usdt|usdc|btc)@kline_(\w+)")
_KLINE_KEYS = ("t", "o", "h", "l", "c", "v")


def parse_kline_message(kline: dict[str, Any], stream: str) -> Kline | None:
    """
    解析币安 K 线推送数据体
    @param kline: 消息体中的 data.k
    @param stream: 消息体中的 stream, 形如 dogeusdt@kline_1m
    @return: 无法解析时返回 None, 由调用方决定日志级别
    """
    match = _KLINE_STREAM_PATTERN.fullmatch(stream)
    if match is None:
        return None

    if any(key not in kline for key in _KLINE_KEYS):
        return None

    return Kline(
        # 币安推送流名全小写, 领域层统一使用大写
        symbol=Symbol(base=match.group(1).upper(), quote=match.group(2).upper()),
        timeframe=match.group(3),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
        timestamp=int(kline["t"]),
        finished=bool(kline.get("x", False)),
    )


def parse_ws_message(message: str) -> Kline | None:
    """
    解析币安 ws 推送消息, 是 ws 字符串 → Kline 的唯一入口
    @param message: 原始 ws 消息字符串
    @return: 无法解析(脏数据/非K线流)时返回 None, 由调用方决定日志级别
    """
    try:
        payload: Any = json.loads(message)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    envelope = payload

    stream = envelope.get("stream", "")
    data = envelope.get("data", {})
    if not isinstance(data, dict):
        return None
    kline = data.get("k")
    if not isinstance(kline, dict):
        return None

    return parse_kline_message(kline, stream)


class Task:
    def __init__(self, name: str):
        self.name: str = name

    def run(self, kline: Kline):
        pass

    def safe_run(self, kline: Kline) -> None:
        """
        执行任务并隔离异常。

        任务运行在共享线程池中, 未捕获的异常会被线程池静默吞掉,
        导致策略停止响应却没有任何日志; 这里统一记录后丢弃该根K线。
        """
        try:
            self.run(kline)
        except Exception:
            logger.exception("任务 %s 处理K线失败", self.name)


class DataEventLoop:
    def __init__(self):
        self.tasks: list[Task] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.running: bool = False

    def add_task(self, task: Task):
        self.tasks.append(task)

    def loop(self, kline: Kline):
        if not self.running:
            return
        for task in self.tasks:
            self.executor.submit(task.safe_run, kline)

    def start(self):
        self.running = True

    def stop(self):
        """停止接收消息并关闭线程池; 由程序入口在退出时调用"""
        self.running = False
        self.executor.shutdown(wait=False)


def create_websocket_session(
    url: str,
    on_open: Callable[[Any], None],
    on_message: Callable[[Any, str], None],
    on_error: Callable[[Any, Exception], None],
    on_close: Callable[[Any, Any, Any], None],
    on_pong: Callable[[Any, str], None],
) -> Any:
    """默认的 ws 会话工厂; 测试中可注入内存实现"""
    return websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_pong=on_pong,
    )


class BinanceDataEventLoop(DataEventLoop):
    SET_PROPERTY_ID = 1
    SUBSCRIBE_KLINE_ID = 2
    WEBSOCKET_URL = "wss://fstream.binance.com/market/stream"

    def __init__(
        self,
        kline_subscribes: list[str],
        session_factory: Callable[..., Any] = create_websocket_session,
        retry_interval: float = 5.0,
        max_connections: int | None = None,
    ):
        """
        @param kline_subscribes: 订阅的流, 形如 dogeusdt@kline_1m
        @param session_factory: ws 会话工厂(测试注入内存实现)
        @param retry_interval: 断线重连间隔(秒)
        @param max_connections: 最多建立多少个连接, None 表示不限
        """
        super().__init__()
        self.kline_subscribes = kline_subscribes
        self.session_factory = session_factory
        self.retry_interval = retry_interval
        self.max_connections = max_connections

    def start(self):
        """建立连接, 断开后自动重连, 直到 stop() 或达到 max_connections"""
        super().start()
        connections = 0
        while self.running:
            if self.max_connections is not None and connections >= self.max_connections:
                logger.info("已达最大连接数 %s, 停止重连", self.max_connections)
                break

            connections += 1
            ws_session = self.session_factory(
                url=self.WEBSOCKET_URL,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_pong=self.on_pong,
            )
            ws_session.run_forever(ping_interval=20, ping_timeout=15)

            if not self.running:
                break
            logger.warning(
                "%ss 后重连(累计第 %s 次连接)", self.retry_interval, connections
            )
            time.sleep(self.retry_interval)

        logger.info("连接循环结束, 累计建立 %s 个连接", connections)

    def _subscribe(self, ws: websocket.WebSocketApp):
        params: dict[str, Any] = {
            "method": "SUBSCRIBE",
            "params": self.kline_subscribes,
            "id": self.SUBSCRIBE_KLINE_ID,
        }
        ws.send(json.dumps(params))
        logger.info("### BinanceDataEventLoop Subscribed ### %s", self.kline_subscribes)

    def on_message(self, ws: websocket.WebSocketApp, message: str):
        kline = parse_ws_message(message)
        if kline is None:
            logger.debug("忽略无法解析的消息: %s", message[:200])
            return
        self.loop(kline)

    def on_error(self, ws: websocket.WebSocketApp, error: Exception):
        logger.error("BinanceDataEventLoop Error: %s", error)

    def on_close(
        self, ws: websocket.WebSocketApp, close_status_code: int | str, close_msg: str
    ):
        """仅记录断开原因: 是否结束由 stop() 决定, start() 负责重连"""
        logger.warning(
            "### BinanceDataEventLoop Closed ### %s: %s",
            close_status_code,
            close_msg,
        )

    def on_open(self, ws: websocket.WebSocketApp):
        logger.info("### BinanceDataEventLoop Opened ###")
        params: dict[str, Any] = {
            "method": "SET_PROPERTY",
            "params": ["combined", True],
            "id": self.SET_PROPERTY_ID,
        }
        ws.send(json.dumps(params))
        self._subscribe(ws)

    def on_pong(self, ws: websocket.WebSocketApp, message: str):
        logger.debug("Pong")
        if random.randint(1, 100) == 1:
            self._subscribe(ws)


class KlineScheduleKind(Enum):
    """TimerDataEventLoop 的调度点类型"""

    MIDPOINT = "midpoint"  # 周期中点: 盘中更新
    BOUNDARY = "boundary"  # 周期边界(+fetch_delay): 拉取已完成K线


class TimerDataEventLoop(DataEventLoop):
    """定时拉取型事件循环: 不建 ws 连接, 由时钟按 timeframe 节奏经 REST 拉取K线

    - 周期边界(+fetch_delay): 拉最后一根已完成K线 → 触发 on_kline_finished;
    - 周期中点: 拉当前进行中K线 → 触发 on_kline;
    - 两者都经 `Task.run(kline)` fan-out, 由 StrategyTask 过滤后走 strategy.run(kline)。
    """

    def __init__(
        self,
        subscriptions: list[tuple[Symbol, str]],
        market: MarketDataPort,
        clock: Callable[[], float] = time.time,
        fetch_delay: float = 2.0,
        poll_interval: float = 0.05,
    ):
        """
        @param subscriptions: (symbol, timeframe) 订阅列表, 不能为空
        @param market: 行情端口(REST 拉取K线)
        @param clock: 时间源(秒), 测试注入假时钟
        @param fetch_delay: 边界后延迟拉取的秒数(等交易所收盘), 必须小于最短周期的一半
        @param poll_interval: 等待调度的轮询间隔(秒), 兼顾 stop() 响应速度
        """
        super().__init__()
        if not subscriptions:
            raise ValueError("subscriptions 不能为空")
        if fetch_delay < 0:
            raise ValueError("fetch_delay 不能为负")

        self.subscriptions = list(subscriptions)
        self.market = market
        self.clock = clock
        self.fetch_delay = fetch_delay
        self.poll_interval = poll_interval
        self._wakeup = threading.Event()
        self._tf_seconds: dict[str, int] = self._validate_timeframes()
        # 最近一次分发的已完成K线时间戳, 按 (symbol, timeframe) 隔离
        self._dispatched_finished: dict[tuple[str, str], int] = {}

    def _validate_timeframes(self) -> dict[str, int]:
        """解析并缓存每个唯一 timeframe 的秒数; 构造期失败快速报错"""
        tf_seconds: dict[str, int] = {}
        for _, timeframe in self.subscriptions:
            if timeframe in tf_seconds:
                continue
            seconds = timeframe_seconds(timeframe)
            if 2 * self.fetch_delay >= seconds:
                raise ValueError(
                    f"fetch_delay({self.fetch_delay}s) 必须小于周期 "
                    f"{timeframe} 的一半({seconds / 2}s), 否则边界事件会迟到越过中点"
                )
            tf_seconds[timeframe] = seconds
        return tf_seconds

    def start(self) -> None:
        """阻塞调度: 到点拉取K线并分发, 直到 stop()(与 BinanceDataEventLoop.start 同语义)"""
        super().start()
        while self.running:
            fire_at, events = self._next_events(self.clock())
            if not self._wait_until(fire_at):
                break
            for timeframe, kind in events:
                self._fire(timeframe, kind, fire_at)

    def stop(self) -> None:
        """停止调度并关闭线程池; 唤醒阻塞中的等待"""
        super().stop()
        self._wakeup.set()

    def _next_events(
        self, now: float
    ) -> tuple[float, list[tuple[str, KlineScheduleKind]]]:
        """纯函数: 最早到期时刻 + 该时刻并列到期的全部事件

        候选 = 当前/下一周期的 中点 与 边界+fetch_delay; fire_at <= now 的事件视为
        已错过, 不补发。并列同刻的事件(如 1m 与 5m 边界重合)一齐返回。
        """
        candidates: list[tuple[float, str, KlineScheduleKind]] = []
        for timeframe in self._tf_seconds:
            tf = self._tf_seconds[timeframe]
            current_boundary = math.floor(now / tf) * tf
            next_boundary = current_boundary + tf
            midpoint_offset = tf / 2
            candidates.extend(
                [
                    (
                        current_boundary + self.fetch_delay,
                        timeframe,
                        KlineScheduleKind.BOUNDARY,
                    ),
                    (
                        next_boundary + self.fetch_delay,
                        timeframe,
                        KlineScheduleKind.BOUNDARY,
                    ),
                    (current_boundary + midpoint_offset, timeframe, KlineScheduleKind.MIDPOINT),
                    (next_boundary + midpoint_offset, timeframe, KlineScheduleKind.MIDPOINT),
                ]
            )

        fire_at = min(fire for fire, _, _ in candidates if fire > now)
        events = [
            (timeframe, kind)
            for fire, timeframe, kind in candidates
            if fire == fire_at
        ]
        return fire_at, events

    def _wait_until(self, target: float) -> bool:
        """分片等待至 clock() 到达 target; stop() 立即唤醒并返回 False"""
        while self.running:
            if self.clock() >= target:
                return True
            if self._wakeup.wait(self.poll_interval):
                return False
        return False

    def _fire(self, timeframe: str, kind: KlineScheduleKind, fire_at: float) -> None:
        """为该周期的每个订阅拉取K线并分发; 单次拉取失败只跳过本周期"""
        tf_seconds = self._tf_seconds[timeframe]
        for symbol, subscription_tf in self.subscriptions:
            if subscription_tf != timeframe:
                continue
            if kind is KlineScheduleKind.BOUNDARY:
                expected_open_ms = (
                    int((fire_at - self.fetch_delay) * 1000) - tf_seconds * 1000
                )
                kline = self._fetch_finished_kline(symbol, timeframe, expected_open_ms)
            else:
                kline = self._fetch_current_kline(symbol, timeframe)
            if kline is not None:
                self.loop(kline)

    def _fetch_finished_kline(
        self, symbol: Symbol, timeframe: str, expected_open_ms: int
    ) -> Kline | None:
        """拉取最后一根已完成K线; 陈旧或已分发过的返回 None, 不等待重试

        - 落后预期边界超过一个周期 → 数据源异常(如 demo 环境把数据集头部当最新
          数据返回), 拒绝分发且不写入去重记录, 数据源恢复后仍能正常分发;
        - 交易所尚未滚出收盘K线(本地时钟落后)或拉取失败 → 直接返回 None,
          本周期不分发, 由下一个边界自然补上;
        - 去重防止同一根K线(时钟漂移/数据迟到)重复触发 on_kline_finished。
        """
        finished = [
            k for k in self._safe_fetch(symbol, timeframe, limit=2) if k.finished
        ]
        if not finished:
            return None
        kline = finished[-1]
        tf_ms = timeframe_seconds(timeframe) * 1000
        behind_ms = expected_open_ms - kline.timestamp
        if behind_ms > tf_ms:
            logger.error(
                "行情数据源返回陈旧K线, 拒绝分发: 期望开仓 %s, 实际 %s (落后 %.0f 个周期), %s %s —— 请检查行情数据源",
                expected_open_ms,
                kline.timestamp,
                behind_ms / tf_ms,
                symbol.ccxt(),
                timeframe,
            )
            return None
        if kline.timestamp != expected_open_ms:
            logger.warning(
                "K线时间戳 %s 与本地边界 %s 不一致(时钟漂移): %s %s",
                kline.timestamp,
                expected_open_ms,
                symbol.ccxt(),
                timeframe,
            )

        key = (symbol.binance(), timeframe)
        if self._dispatched_finished.get(key) == kline.timestamp:
            return None
        self._dispatched_finished[key] = kline.timestamp
        return kline

    def _fetch_current_kline(self, symbol: Symbol, timeframe: str) -> Kline | None:
        """拉取当前进行中的K线(盘中更新); 拉到已收盘K线视为时钟漂移, 跳过"""
        klines = self._safe_fetch(symbol, timeframe, limit=1)
        if not klines:
            return None
        kline = klines[-1]
        if kline.finished:
            logger.warning(
                "中点拉到已收盘K线(本地时钟落后或数据源陈旧) %s %s, 时间戳 %s, 跳过盘中更新",
                symbol.ccxt(),
                timeframe,
                kline.timestamp,
            )
            return None
        return kline

    def _safe_fetch(self, symbol: Symbol, timeframe: str, *, limit: int) -> list[Kline]:
        """拉取K线; 失败记日志返回空列表, 不让单次失败中断调度"""
        try:
            return self.market.fetch_ohlcv(symbol, timeframe, limit)
        except Exception:
            logger.warning(
                "K线拉取失败 %s %s (limit=%s)",
                symbol.ccxt(),
                timeframe,
                limit,
                exc_info=True,
            )
            return []
