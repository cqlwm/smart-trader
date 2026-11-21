"""策略运行时: 与控制台同进程, 按策略配置的 data_source 装配事件循环

- `DataSource.WS` → `BinanceDataEventLoop`（WebSocket 推送）;
- `DataSource.TIMER` → `TimerDataEventLoop`（REST 定时拉取）;
- 两种循环可同时存在, 各自在守护线程中阻塞运行;
- 没有启用配置或装配失败时只记录日志, 控制台仍然可用;
- 传入 `configs_provider` 后启动配置监督线程: 运行中热新增策略、
  热更新调参; symbol / timeframe / position_side / master_side 等
  身份字段与停用仍要求重启（见 `.memory/01` 第十五节）;
- 停止顺序: 先停配置监督与命令轮询（不再接受新配置 / 命令）,
  再叫停事件循环并 join 线程。
"""

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from smart_trader import log
from smart_trader.data_event_loop import (
    BinanceDataEventLoop,
    DataEventLoop,
    TimerDataEventLoop,
)
from smart_trader.exchange.ports import Exchange, MarketDataPort
from smart_trader.model import (
    DataSource,
    DisablePositionPolicy,
    StrategyLifecycle,
    Symbol,
)
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.config_store import ConfigStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_loader import (
    build_strategy_tasks,
    should_load,
)
from smart_trader.strategy.config_payload import StrategyConfigPayload
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from smart_trader.task.command_runner import CommandRunner, controllable_strategies
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

WsLoopFactory = Callable[[list[str]], DataEventLoop]
TimerLoopFactory = Callable[[list[tuple[Symbol, str]], MarketDataPort], DataEventLoop]
ConfigsProvider = Callable[[], list[StrategyConfigRecord]]

_LOOP_JOIN_TIMEOUT = 5.0


def _create_ws_loop(subscribes: list[str]) -> DataEventLoop:
    return BinanceDataEventLoop(kline_subscribes=subscribes)


def _create_timer_loop(
    subscriptions: list[tuple[Symbol, str]], market: MarketDataPort
) -> DataEventLoop:
    return TimerDataEventLoop(subscriptions=subscriptions, market=market)


@dataclass
class _RunningStrategy:
    """运行中的策略任务快照（热更新按 version 判断是否变化）"""

    task: StrategyTask
    loop: DataEventLoop
    data_source: DataSource
    version: int
    payload: str


class StrategyRuntime:
    """按配置装配策略任务与事件循环, 并管理其生命周期

    @param records 策略配置行（是否启用、data_source 均由行自身决定）
    @param exchange_factory 交易所构造入口; 仅在存在启用配置时调用
    @param journal 账本（策略恢复活跃仓位）
    @param command_store 控制命令仓储
    @param registry 运行态心跳
    @param configs_provider 配置读取入口（通常是 `ConfigStore.list`）;
        提供后启用配置热新增 / 调参热更新
    @param lifecycle_store 配置写入入口; 提供后由运行时推进停用流程
    @param config_poll_interval 配置轮询间隔（秒）
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
        configs_provider: ConfigsProvider | None = None,
        lifecycle_store: ConfigStore | None = None,
        config_poll_interval: float = 2.0,
        ws_loop_factory: WsLoopFactory = _create_ws_loop,
        timer_loop_factory: TimerLoopFactory = _create_timer_loop,
    ) -> None:
        self._records = list(records)
        self._exchange_factory = exchange_factory
        self._journal = journal
        self._command_store = command_store
        self._registry = registry
        self._poll_interval = poll_interval
        self._configs_provider = configs_provider
        self._lifecycle_store = lifecycle_store
        self._config_poll_interval = config_poll_interval
        self._ws_loop_factory = ws_loop_factory
        self._timer_loop_factory = timer_loop_factory

        self._started = False
        self._loops: list[DataEventLoop] = []
        self._threads: list[threading.Thread] = []
        self._command_runner: CommandRunner | None = None
        self._loops_by_source: dict[DataSource, DataEventLoop] = {}
        self._running: dict[str, _RunningStrategy] = {}
        self._supervisor_thread: threading.Thread | None = None
        self._supervisor_stop = threading.Event()
        self._sync_lock = threading.Lock()
        self._exchange: Exchange | None = None
        self._exchange_failure_signature: tuple[tuple[str, int], ...] | None = None
        # 构造失败 / 需要重启的配置版本: 避免轮询线程每轮重复日志与重试
        self._failed_versions: dict[str, int] = {}
        self._restart_notified: dict[str, int] = {}
        # 停用流程里的一次性动作（request_close_all / abandon）只执行一次
        self._disable_action_done: set[str] = set()

    def start(self) -> None:
        """启动策略运行时; 幂等, 装配失败只记录日志并保持控制台可用"""
        if self._started:
            return
        self._started = True
        self._supervisor_stop.clear()
        self._failed_versions.clear()
        self._restart_notified.clear()
        self._disable_action_done.clear()

        if self._configs_provider is not None:
            try:
                self._start_supervisor()
            except Exception:
                logger.exception("策略运行时启动失败, 控制台继续运行")
                self.stop()
            return

        enabled = [record for record in self._records if should_load(record)]
        if not enabled:
            logger.warning("没有启用的策略配置, 控制台以只读模式启动")
            return

        try:
            exchange = self._exchange_factory()
            self._exchange = exchange
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
                ws_loop = self._ws_loop_factory(_ws_subscriptions(ws_tasks))
                self._start_loop("ws-event-loop", ws_loop, ws_tasks)
                self._loops_by_source[DataSource.WS] = ws_loop
            if timer_tasks:
                timer_loop = self._timer_loop_factory(
                    _timer_subscriptions(timer_tasks), exchange
                )
                self._start_loop("timer-event-loop", timer_loop, timer_tasks)
                self._loops_by_source[DataSource.TIMER] = timer_loop
            logger.info(
                "策略运行时启动: ws=%s timer=%s",
                len(ws_tasks),
                len(timer_tasks),
            )
        except Exception:
            logger.exception("策略运行时启动失败, 控制台继续运行")
            self.stop()

    def _start_supervisor(self) -> None:
        """启动命令执行器与配置监督线程（控制台 + 运行时同进程）"""
        runner = CommandRunner(
            self._command_store,
            {},
            poll_interval=self._poll_interval,
            registry=self._registry,
        )
        runner.start()
        self._command_runner = runner
        self._supervisor_thread = threading.Thread(
            target=self._supervisor_loop, name="ConfigSupervisor", daemon=True
        )
        self._supervisor_thread.start()
        try:
            self.sync_configs_once()
        except Exception:
            logger.exception("初始策略配置热加载失败, 控制台继续运行")

    def _supervisor_loop(self) -> None:
        while not self._supervisor_stop.is_set():
            try:
                self.sync_configs_once()
            except Exception:
                logger.exception("策略配置热同步失败")
            self._supervisor_stop.wait(self._config_poll_interval)

    def sync_configs_once(self) -> None:
        """同步一次数据库配置: 热新增 / 调参热更新（供轮询线程与测试调用）"""
        if not self._started or self._configs_provider is None:
            return
        with self._sync_lock:
            records = list(self._configs_provider())
            current = {record.strategy_key: record for record in records}
            loadable = [record for record in records if should_load(record)]
            pending = [
                record
                for record in loadable
                if record.strategy_key not in self._running
                and self._failed_versions.get(record.strategy_key) != record.version
            ]
            if pending:
                exchange = self._exchange_for(loadable)
                if exchange is not None:
                    for record in pending:
                        self._add_strategy(record, exchange)
            for key, running in list(self._running.items()):
                record = current.get(key)
                if record is None:
                    self._notify_restart(key, -1, "配置已删除")
                    continue
                if record.version != running.version:
                    self._update_strategy(key, running, record)
                self._supervise_lifecycle(key, running, record)

    def _exchange_for(
        self, enabled: list[StrategyConfigRecord]
    ) -> Exchange | None:
        """按需构造交易所; 同一配置集合构造失败只告警一次"""
        signature = tuple(
            sorted((record.strategy_key, record.version) for record in enabled)
        )
        if self._exchange_failure_signature == signature:
            return None
        try:
            if self._exchange is None:
                self._exchange = self._exchange_factory()
        except Exception:
            logger.exception("交易所构造失败, 热加载暂停; 配置变化后重试")
            self._exchange_failure_signature = signature
            return None
        self._exchange_failure_signature = None
        return self._exchange

    def _add_strategy(
        self, record: StrategyConfigRecord, exchange: Exchange
    ) -> None:
        key = record.strategy_key
        try:
            payload = StrategyConfigPayload.from_json(record.payload)
            tasks = build_strategy_tasks(
                [record],
                data_source=payload.data_source,
                exchange=exchange,
                journal=self._journal,
            )
            if not tasks:
                self._failed_versions[key] = record.version
                return
            task = tasks[0]
        except Exception:
            logger.exception("策略热加载失败: %s (v%s)", key, record.version)
            self._failed_versions[key] = record.version
            return

        try:
            loop = self._loops_by_source.get(payload.data_source)
            if loop is None:
                loop = self._create_loop(payload.data_source, task, exchange)
                self._loops_by_source[payload.data_source] = loop
            else:
                self._register_subscription(loop, task, payload.data_source)
                loop.add_task(task)
        except Exception:
            logger.exception("策略热新增挂载失败: %s (v%s)", key, record.version)
            self._failed_versions[key] = record.version
            return

        self._running[key] = _RunningStrategy(
            task=task,
            loop=loop,
            data_source=payload.data_source,
            version=record.version,
            payload=record.payload,
        )
        self._register_command(task)
        logger.info(
            "策略热新增: %s (v%s, %s)",
            key,
            record.version,
            payload.data_source.value,
        )

    def _create_loop(
        self, data_source: DataSource, task: StrategyTask, exchange: Exchange
    ) -> DataEventLoop:
        """为新的 data_source 创建事件循环并启动守护线程"""
        if data_source is DataSource.WS:
            loop = self._ws_loop_factory(_ws_subscriptions([task]))
            name = "ws-event-loop"
        else:
            loop = self._timer_loop_factory(_timer_subscriptions([task]), exchange)
            name = "timer-event-loop"
        self._start_loop(name, loop, [task])
        return loop

    @staticmethod
    def _register_subscription(
        loop: DataEventLoop, task: StrategyTask, data_source: DataSource
    ) -> None:
        """把新任务的 symbol/timeframe 加入已有循环的订阅"""
        for timeframe in task.timeframes:
            if data_source is DataSource.WS:
                stream = task.symbol.binance_ws_sub_kline(timeframe)
                if loop.add_kline_stream(stream):
                    logger.info("热新增行情订阅: %s", stream)
            else:
                if loop.add_timer_subscription(task.symbol, timeframe):
                    logger.info(
                        "热新增定时订阅: %s %s", task.symbol.ccxt(), timeframe
                    )

    def _register_command(self, task: StrategyTask) -> None:
        runner = self._command_runner
        if runner is None:
            return
        for strategy in controllable_strategies([task]).values():
            runner.add_strategy(strategy)

    def _update_strategy(
        self, key: str, running: _RunningStrategy, record: StrategyConfigRecord
    ) -> None:
        if self._failed_versions.get(key) == record.version:
            return
        if record.lifecycle in (
            StrategyLifecycle.DISABLING,
            StrategyLifecycle.GUARD,
        ):
            running.version = record.version
            running.payload = record.payload
            self._supervise_lifecycle(key, running, record)
            return
        if not record.enabled:
            # 历史停用（lifecycle=disabled）: 只保持暂停, 不再要求重启
            self._pause_running(running)
            running.version = record.version
            running.payload = record.payload
            return
        if record.payload == running.payload:
            running.version = record.version
            self._apply_desired_running(running, record)
            return
        try:
            payload = StrategyConfigPayload.from_json(record.payload)
            new_config = payload.to_strategy_config(key)
        except Exception:
            logger.exception(
                "策略热更新配置解析失败: %s (v%s)", key, record.version
            )
            self._failed_versions[key] = record.version
            return
        if payload.data_source is not running.data_source:
            self._notify_restart(key, record.version, "data_source 变更")
            running.version = record.version
            return

        strategy = running.task.strategy
        if not isinstance(strategy, SignalGridStrategy):
            self._notify_restart(
                key,
                record.version,
                f"策略类型 {type(strategy).__name__} 不支持热更新",
            )
            running.version = record.version
            return
        changed = [
            field
            for field in ("symbol", "timeframe", "position_side", "master_side")
            if getattr(strategy.config, field) != getattr(new_config, field)
        ]
        if changed:
            self._notify_restart(key, record.version, f"{', '.join(changed)} 变更")
            running.version = record.version
            return
        try:
            strategy.apply_config(new_config)
        except ValueError as exc:
            self._notify_restart(key, record.version, str(exc))
            running.version = record.version
            return
        except Exception:
            logger.exception(
                "策略热更新失败, 保留旧配置: %s (v%s)", key, record.version
            )
            running.version = record.version
            return

        running.version = record.version
        running.payload = record.payload
        self._apply_desired_running(running, record)
        if self._command_runner is not None:
            self._command_runner.heartbeat()
        logger.info("策略热更新完成: %s (v%s)", key, record.version)

    def _pause_running(self, running: _RunningStrategy) -> None:
        strategy = running.task.strategy
        if isinstance(strategy, SignalGridStrategy) and strategy.is_running:
            strategy.pause()

    def _apply_desired_running(
        self, running: _RunningStrategy, record: StrategyConfigRecord
    ) -> None:
        """按持久化运行意图同步暂停状态（重启后保持暂停）"""
        strategy = running.task.strategy
        if not isinstance(strategy, SignalGridStrategy):
            return
        if record.lifecycle is not StrategyLifecycle.ACTIVE:
            self._pause_running(running)
            return
        if record.desired_running and not strategy.is_running:
            strategy.resume()
        elif not record.desired_running and strategy.is_running:
            strategy.pause()

    def _supervise_lifecycle(
        self, key: str, running: _RunningStrategy, record: StrategyConfigRecord
    ) -> None:
        """推进停用流程: 暂停 → 按 policy 处置持仓 → guard / disabled"""
        strategy = running.task.strategy
        if not isinstance(strategy, SignalGridStrategy):
            return
        lifecycle = record.lifecycle
        if lifecycle is StrategyLifecycle.ACTIVE:
            self._disable_action_done.discard(key)
            self._apply_desired_running(running, record)
            return
        if lifecycle is StrategyLifecycle.DISABLING:
            self._pause_running(running)
            policy = record.disable_policy
            if policy is None:
                self._complete_disable(
                    key, StrategyLifecycle.DISABLED, error="缺少停用策略"
                )
                return
            if policy is DisablePositionPolicy.CLOSE_ALL:
                if key not in self._disable_action_done:
                    strategy.request_close_all()
                    self._disable_action_done.add(key)
                    logger.warning("停用开始, 等待全平: %s", key)
                if strategy.is_flat():
                    self._complete_disable(key, StrategyLifecycle.DISABLED)
                return
            if key not in self._disable_action_done:
                if policy is DisablePositionPolicy.LIMIT_EXIT:
                    strategy.request_close_all()
                elif policy is DisablePositionPolicy.ABANDON:
                    count = strategy.abandon_positions()
                    logger.warning(
                        "停用抛弃: %s, 本地归档 %s 个仓位; 交易所持仓/挂单未处理",
                        key,
                        count,
                    )
                self._disable_action_done.add(key)
            target = (
                StrategyLifecycle.DISABLED
                if policy is DisablePositionPolicy.ABANDON
                else StrategyLifecycle.GUARD
            )
            self._complete_disable(key, target)
            return
        if lifecycle is StrategyLifecycle.GUARD:
            # 守护态: 保持暂停; limit_exit 在重启后需要重新挂一次出场单
            self._pause_running(running)
            if (
                record.disable_policy is DisablePositionPolicy.LIMIT_EXIT
                and key not in self._disable_action_done
            ):
                strategy.request_close_all()
                self._disable_action_done.add(key)
            return
        # disabled: 保持暂停, 直到重新启用
        self._pause_running(running)
        self._disable_action_done.discard(key)

    def _complete_disable(
        self,
        key: str,
        lifecycle: StrategyLifecycle,
        *,
        error: str | None = None,
    ) -> None:
        if self._lifecycle_store is None:
            logger.warning(
                "缺少 lifecycle_store, 停用流程无法完成: %s -> %s",
                key,
                lifecycle.value,
            )
            return
        updated = self._lifecycle_store.complete_disable(
            key, lifecycle, error=error
        )
        if updated is None:
            logger.warning(
                "停用完成写入被并发状态变更忽略: %s -> %s",
                key,
                lifecycle.value,
            )
            return
        logger.warning(
            "停用流程完成: %s -> %s", key, lifecycle.value
        )

    def _notify_restart(self, key: str, version: int, reason: str) -> None:
        if self._restart_notified.get(key) == version:
            return
        self._restart_notified[key] = version
        logger.warning(
            "策略配置变更需重启 run.py 才生效: %s (v%s, %s)",
            key,
            version,
            reason,
        )

    def stop(self) -> None:
        """停止策略运行时; 幂等, 停止前未启动时为空操作"""
        supervisor = self._supervisor_thread
        if supervisor is not None:
            self._supervisor_stop.set()
            supervisor.join(timeout=_LOOP_JOIN_TIMEOUT)
            self._supervisor_thread = None

        if not self._loops and self._command_runner is None:
            self._started = False
            self._running.clear()
            self._loops_by_source.clear()
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
        self._running.clear()
        self._loops_by_source.clear()
        self._exchange = None
        self._exchange_failure_signature = None
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
