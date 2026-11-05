"""策略运行时: 与控制台同进程, 按策略配置的 data_source 装配事件循环

- `DataSource.WS` → `BinanceDataEventLoop`（WebSocket 推送）;
- `DataSource.TIMER` → `TimerDataEventLoop`（REST 定时拉取）;
- 两种循环可同时存在, 各自在守护线程中阻塞运行;
- 没有启用配置或装配失败时只记录日志, 控制台仍然可用;
- 停止顺序: 先停命令轮询（不再接受新命令）, 再叫停事件循环并 join 线程。
"""

import threading
from collections.abc import Callable, Iterable

from smart_trader import log
from smart_trader.data_event_loop import (
    BinanceDataEventLoop,
    DataEventLoop,
    TimerDataEventLoop,
)
from smart_trader.exchange.ports import Exchange, MarketDataPort
from smart_trader.model import DataSource, Symbol
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_loader import build_strategy_tasks
from smart_trader.task.command_runner import CommandRunner, controllable_strategies
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

WsLoopFactory = Callable[[list[str]], DataEventLoop]
TimerLoopFactory = Callable[[list[tuple[Symbol, str]], MarketDataPort], DataEventLoop]

_LOOP_JOIN_TIMEOUT = 5.0


def _create_ws_loop(subscribes: list[str]) -> DataEventLoop:
    return BinanceDataEventLoop(kline_subscribes=subscribes)


def _create_timer_loop(
    subscriptions: list[tuple[Symbol, str]], market: MarketDataPort
) -> DataEventLoop:
    return TimerDataEventLoop(subscriptions=subscriptions, market=market)


class StrategyRuntime:
    """按配置装配策略任务与事件循环, 并管理其生命周期

    @param records 策略配置行（是否启用、data_source 均由行自身决定）
    @param exchange_factory 交易所构造入口; 仅在存在启用配置时调用
    @param journal 账本（策略恢复活跃仓位）
    @param command_store 控制命令仓储
    @param registry 运行态心跳
    """

    def __init__(
        self,
        records: Iterable[StrategyConfigRecord],
        *,
        exchange_factory: Callable[[], Exchange],
        journal: OrderJournal,
        command_store: CommandStore,
        registry: RuntimeRegistry,
        poll_interval: float = 2.0,
        ws_loop_factory: WsLoopFactory = _create_ws_loop,
        timer_loop_factory: TimerLoopFactory = _create_timer_loop,
    ) -> None:
        self._records = list(records)
        self._exchange_factory = exchange_factory
        self._journal = journal
        self._command_store = command_store
        self._registry = registry
        self._poll_interval = poll_interval
        self._ws_loop_factory = ws_loop_factory
        self._timer_loop_factory = timer_loop_factory

        self._started = False
        self._loops: list[DataEventLoop] = []
        self._threads: list[threading.Thread] = []
        self._command_runner: CommandRunner | None = None

    def start(self) -> None:
        """启动策略运行时; 幂等, 装配失败只记录日志并保持控制台可用"""
        if self._started:
            return
        self._started = True

        enabled = [record for record in self._records if record.enabled]
        if not enabled:
            logger.warning("没有启用的策略配置, 控制台以只读模式启动")
            return

        try:
            exchange = self._exchange_factory()
            ws_tasks = build_strategy_tasks(
                enabled,
                data_source=DataSource.WS,
                exchange=exchange,
                journal=self._journal,
            )
            timer_tasks = build_strategy_tasks(
                enabled,
                data_source=DataSource.TIMER,
                exchange=exchange,
                journal=self._journal,
            )
            tasks = ws_tasks + timer_tasks
            if not tasks:
                logger.warning("没有可启动的策略任务, 控制台以只读模式启动")
                return

            runner = CommandRunner(
                self._command_store,
                controllable_strategies(tasks),
                poll_interval=self._poll_interval,
                registry=self._registry,
            )
            runner.start()
            self._command_runner = runner

            if ws_tasks:
                self._start_loop(
                    "ws-event-loop",
                    self._ws_loop_factory(_ws_subscriptions(ws_tasks)),
                    ws_tasks,
                )
            if timer_tasks:
                self._start_loop(
                    "timer-event-loop",
                    self._timer_loop_factory(
                        _timer_subscriptions(timer_tasks), exchange
                    ),
                    timer_tasks,
                )
            logger.info(
                "策略运行时启动: ws=%s timer=%s",
                len(ws_tasks),
                len(timer_tasks),
            )
        except Exception:
            logger.exception("策略运行时启动失败, 控制台继续运行")
            self.stop()

    def stop(self) -> None:
        """停止策略运行时; 幂等, 停止前未启动时为空操作"""
        if not self._loops and self._command_runner is None:
            self._started = False
            return

        runner = self._command_runner
        if runner is not None:
            try:
                runner.stop()
            except Exception:
                logger.exception("命令执行器停止失败")
            self._command_runner = None

        for loop in self._loops:
            try:
                loop.stop()
            except Exception:
                logger.exception("事件循环停止失败")
        for thread in self._threads:
            thread.join(timeout=_LOOP_JOIN_TIMEOUT)

        self._loops.clear()
        self._threads.clear()
        self._started = False

    def _start_loop(
        self, name: str, loop: DataEventLoop, tasks: list[StrategyTask]
    ) -> None:
        for task in tasks:
            loop.add_task(task)
        self._loops.append(loop)
        thread = threading.Thread(
            target=self._run_loop, args=(name, loop), name=name, daemon=True
        )
        self._threads.append(thread)
        thread.start()

    @staticmethod
    def _run_loop(name: str, loop: DataEventLoop) -> None:
        try:
            loop.start()
        except Exception:
            logger.exception("事件循环 %s 异常退出", name)


def _ws_subscriptions(tasks: Iterable[StrategyTask]) -> list[str]:
    subscriptions: list[str] = []
    for task in tasks:
        for timeframe in task.timeframes:
            stream = task.symbol.binance_ws_sub_kline(timeframe)
            if stream not in subscriptions:
                subscriptions.append(stream)
    return subscriptions


def _timer_subscriptions(tasks: Iterable[StrategyTask]) -> list[tuple[Symbol, str]]:
    subscriptions: list[tuple[Symbol, str]] = []
    for task in tasks:
        for timeframe in task.timeframes:
            subscription = (task.symbol, timeframe)
            if subscription not in subscriptions:
                subscriptions.append(subscription)
    return subscriptions
