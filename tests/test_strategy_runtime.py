"""StrategyRuntime 装配与生命周期测试

- 未启用配置: 不构造交易所, 仅启动控制台
- 每个 data_source 独立装配, 空数据源不建循环
- 启停幂等, 停止后清理心跳
- 策略装配失败只记录日志, 不影响控制台启动
"""

import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from smart_trader.data_event_loop import DataEventLoop, Task
from smart_trader.exchange.ports import Exchange, MarketDataPort
from smart_trader.model import (
    DataSource,
    Kline,
    OrderRequest,
    OrderResult,
    Position,
    Symbol,
    SymbolInfo,
)
from smart_trader.runtime import RuntimeRegistry, RuntimeSnapshot
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_payload import StrategyConfigPayload
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from smart_trader.strategy_runtime import StrategyRuntime
from smart_trader.task.strategy_task import StrategyTask


class _UnusedExchange(Exchange):
    """只满足端口签名; 装配期不应触达行情 / 交易 / 账户"""

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        raise AssertionError("装配期不得查询交易规则")

    def latest_price(self, symbol: Symbol) -> float:
        raise AssertionError("装配期不得查询行情")

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        raise AssertionError("装配期不得拉取K线")

    def place(self, request: OrderRequest) -> OrderResult:
        raise AssertionError("装配期不得下单")

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return None

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return None

    def balance(self, coin: str) -> float:
        return 0.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        return []


class _RecordingLoop(DataEventLoop):
    """记录 start/stop 调用的内存事件循环"""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0
        self.started = threading.Event()

    def start(self) -> None:
        self.start_calls += 1
        self.started.set()
        super().start()

    def stop(self) -> None:
        self.stop_calls += 1
        super().stop()


class RecordingWsLoop(_RecordingLoop):
    def __init__(self, subscribes: list[str]) -> None:
        super().__init__()
        self.subscribes = subscribes
        self.added_streams: list[str] = []

    def add_kline_stream(self, stream: str) -> bool:
        self.added_streams.append(stream)
        if stream in self.subscribes:
            return False
        self.subscribes.append(stream)
        return True


class RecordingTimerLoop(_RecordingLoop):
    def __init__(
        self, subscriptions: list[tuple[Symbol, str]], market: MarketDataPort
    ) -> None:
        super().__init__()
        self.subscriptions = subscriptions
        self.market = market
        self.added_subscriptions: list[tuple[Symbol, str]] = []

    def add_timer_subscription(self, symbol: Symbol, timeframe: str) -> bool:
        pair = (symbol, timeframe)
        self.added_subscriptions.append(pair)
        if pair in self.subscriptions:
            return False
        self.subscriptions.append(pair)
        return True


class RuntimeFactory:
    """构造 StrategyRuntime 并在用例结束时统一停止, 避免遗留线程"""

    def __init__(
        self, journal: OrderJournal, commands: CommandStore, registry: RuntimeRegistry
    ) -> None:
        self._journal = journal
        self._commands = commands
        self.registry = registry
        self.created: list[StrategyRuntime] = []

    def __call__(
        self,
        records: list[StrategyConfigRecord],
        *,
        exchange_factory: Callable[[], Exchange],
        ws_loops: list[RecordingWsLoop] | None = None,
        timer_loops: list[RecordingTimerLoop] | None = None,
        configs_provider: Callable[[], list[StrategyConfigRecord]] | None = None,
    ) -> StrategyRuntime:
        ws_created: list[RecordingWsLoop] = [] if ws_loops is None else ws_loops
        timer_created: list[RecordingTimerLoop] = (
            [] if timer_loops is None else timer_loops
        )

        def ws_loop_factory(subscribes: list[str]) -> DataEventLoop:
            loop = RecordingWsLoop(subscribes)
            ws_created.append(loop)
            return loop

        def timer_loop_factory(
            subscriptions: list[tuple[Symbol, str]], market: MarketDataPort
        ) -> DataEventLoop:
            loop = RecordingTimerLoop(subscriptions, market)
            timer_created.append(loop)
            return loop

        runtime = StrategyRuntime(
            records,
            exchange_factory=exchange_factory,
            journal=self._journal,
            command_store=self._commands,
            registry=self.registry,
            configs_provider=configs_provider,
            # 测试手动调用 sync_configs_once, 监督线程不参与竞态
            config_poll_interval=3600.0,
            ws_loop_factory=ws_loop_factory,
            timer_loop_factory=timer_loop_factory,
        )
        self.created.append(runtime)
        return runtime

    def cleanup(self) -> None:
        for runtime in self.created:
            runtime.stop()


@pytest.fixture
def runtime_factory(tmp_path: Path) -> Iterator[RuntimeFactory]:
    db_path = tmp_path / "smart_trader.db"
    journal = OrderJournal(str(db_path))
    commands = CommandStore(str(db_path))
    registry = RuntimeRegistry(tmp_path / "runtime.json")
    factory = RuntimeFactory(journal, commands, registry)
    yield factory
    factory.cleanup()
    commands.close()
    journal.close()


def _record(
    strategy_key: str,
    data_source: DataSource,
    *,
    enabled: bool = True,
    symbol: str = "doge/usdt",
    timeframe: str = "1m",
    version: int = 1,
    grid_spacing_rate: float = 0.01,
) -> StrategyConfigRecord:
    payload = StrategyConfigPayload(
        symbol=symbol,
        timeframe=timeframe,
        data_source=data_source,
        max_order=3,
        grid_spacing_rate=grid_spacing_rate,
    )
    return StrategyConfigRecord(
        strategy_key=strategy_key,
        payload=payload.to_json(),
        enabled=enabled,
        version=version,
    )


def test_no_enabled_configs_skips_exchange_and_loops(
    runtime_factory: RuntimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exchange_calls: list[bool] = []

    def exchange_factory() -> Exchange:
        exchange_calls.append(True)
        return _UnusedExchange()

    runtime = runtime_factory(
        [_record("disabled", DataSource.TIMER, enabled=False)],
        exchange_factory=exchange_factory,
    )
    with caplog.at_level(logging.WARNING):
        runtime.start()

    assert exchange_calls == [], "没有启用配置时不得构造交易所"
    assert runtime_factory.created[0] is runtime
    assert "没有启用的策略配置" in caplog.text


def test_ws_config_starts_only_ws_loop(runtime_factory: RuntimeFactory) -> None:
    ws_loops: list[RecordingWsLoop] = []
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [_record("ws_strategy", DataSource.WS)],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
        timer_loops=timer_loops,
    )
    runtime.start()

    assert len(ws_loops) == 1, "ws 配置必须装配一个 ws 事件循环"
    loop = ws_loops[0]
    assert loop.started.wait(1.0), "ws 事件循环必须在后台线程启动"
    assert loop.subscribes == ["dogeusdt@kline_1m"]
    assert [task.name for task in loop.tasks] == ["StrategyTask(DOGE/USDT|1m)"]
    assert timer_loops == [], "没有 timer 配置时不得创建 timer 循环"


def test_timer_config_starts_only_timer_loop(runtime_factory: RuntimeFactory) -> None:
    ws_loops: list[RecordingWsLoop] = []
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [_record("timer_strategy", DataSource.TIMER)],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
        timer_loops=timer_loops,
    )
    runtime.start()

    assert ws_loops == [], "没有 ws 配置时不得创建 ws 循环"
    assert len(timer_loops) == 1
    loop = timer_loops[0]
    assert loop.started.wait(1.0)
    assert loop.subscriptions == [(Symbol(base="DOGE", quote="USDT"), "1m")]
    assert [task.name for task in loop.tasks] == ["StrategyTask(DOGE/USDT|1m)"]


def test_mixed_sources_start_both_loops_and_register_commands(
    runtime_factory: RuntimeFactory,
) -> None:
    ws_loops: list[RecordingWsLoop] = []
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [
            _record("ws_strategy", DataSource.WS),
            _record("timer_strategy", DataSource.TIMER),
        ],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
        timer_loops=timer_loops,
    )
    runtime.start()

    assert len(ws_loops) == 1, "混合数据源必须同时启动两个循环"
    assert len(timer_loops) == 1
    snapshot = _heartbeat(runtime_factory)
    assert snapshot is not None, "CommandRunner 启动后必须写入运行态心跳"
    assert sorted(strategy.strategy_key for strategy in snapshot.strategies) == [
        "timer_strategy",
        "ws_strategy",
    ]


def _heartbeat(runtime_factory: RuntimeFactory) -> RuntimeSnapshot | None:
    """读取心跳快照; 运行态由 CommandRunner 同步写入"""
    return runtime_factory.registry.read()


def test_start_is_idempotent(runtime_factory: RuntimeFactory) -> None:
    ws_loops: list[RecordingWsLoop] = []
    runtime = runtime_factory(
        [_record("ws_strategy", DataSource.WS)],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
    )
    runtime.start()
    runtime.start()

    assert len(ws_loops) == 1, "重复 start() 不得创建第二个事件循环"
    assert ws_loops[0].start_calls == 1


def test_stop_cleans_loops_and_heartbeat(runtime_factory: RuntimeFactory) -> None:
    ws_loops: list[RecordingWsLoop] = []
    runtime = runtime_factory(
        [_record("ws_strategy", DataSource.WS)],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
    )
    runtime.start()
    assert ws_loops[0].started.wait(1.0)

    runtime.stop()

    assert ws_loops[0].stop_calls == 1, "停止时必须叫停事件循环"
    assert ws_loops[0].running is False
    snapshot = _heartbeat(runtime_factory)
    assert snapshot is None, "正常停止后必须清除运行态心跳"


def test_stop_before_start_is_noop(runtime_factory: RuntimeFactory) -> None:
    runtime = runtime_factory(
        [_record("ws_strategy", DataSource.WS)],
        exchange_factory=_UnusedExchange,
    )
    runtime.stop()
    assert _heartbeat(runtime_factory) is None


def test_strategy_startup_failure_keeps_console_alive(
    runtime_factory: RuntimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    def exchange_factory() -> Exchange:
        raise RuntimeError("凭据缺失")

    runtime = runtime_factory(
        [_record("ws_strategy", DataSource.WS)],
        exchange_factory=exchange_factory,
    )
    with caplog.at_level(logging.ERROR):
        runtime.start()

    assert "策略运行时启动失败" in caplog.text
    assert _heartbeat(runtime_factory) is None


def test_invalid_payload_keeps_console_alive(
    runtime_factory: RuntimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    records = [
        StrategyConfigRecord(strategy_key="broken", payload="{not json", enabled=True)
    ]
    runtime = runtime_factory(records, exchange_factory=_UnusedExchange)
    with caplog.at_level(logging.ERROR):
        runtime.start()

    assert "策略运行时启动失败" in caplog.text
    assert _heartbeat(runtime_factory) is None


# ------------------------------------------------------------- 配置热新增 / 热更新


def _strategy_of(task: Task) -> SignalGridStrategy:
    assert isinstance(task, StrategyTask), (
        f"应装配 StrategyTask, 实际 {type(task).__name__}"
    )
    strategy = task.strategy
    assert isinstance(strategy, SignalGridStrategy), (
        f"应装配 SignalGridStrategy, 实际 {type(strategy).__name__}"
    )
    return strategy


def test_hot_add_timer_strategy_builds_loop_and_registers_command(
    runtime_factory: RuntimeFactory,
) -> None:
    state: list[StrategyConfigRecord] = []
    exchange_calls: list[bool] = []
    timer_loops: list[RecordingTimerLoop] = []

    def exchange_factory() -> Exchange:
        exchange_calls.append(True)
        return _UnusedExchange()

    runtime = runtime_factory(
        [],
        exchange_factory=exchange_factory,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()
    assert exchange_calls == [], "启动时无启用配置, 不得构造交易所"

    state.append(_record("hot_timer", DataSource.TIMER))
    runtime.sync_configs_once()

    assert exchange_calls == [True], "热新增首个策略时才构造交易所"
    assert len(timer_loops) == 1, "应创建 timer 事件循环"
    assert [task.name for task in timer_loops[0].tasks] == [
        "StrategyTask(DOGE/USDT|1m)"
    ]
    snapshot = _heartbeat(runtime_factory)
    assert snapshot is not None
    assert [item.strategy_key for item in snapshot.strategies] == ["hot_timer"], (
        "热新增策略必须进入命令执行器与运行态心跳"
    )


def test_hot_add_to_existing_timer_loop_adds_task_and_subscription(
    runtime_factory: RuntimeFactory,
) -> None:
    state = [_record("first", DataSource.TIMER)]
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()
    assert len(timer_loops) == 1

    state.append(_record("second", DataSource.TIMER, symbol="btc/usdt"))
    runtime.sync_configs_once()

    assert len(timer_loops) == 1, "同 data_source 不得重复创建事件循环"
    loop = timer_loops[0]
    assert len(loop.tasks) == 2, "新策略任务必须挂到既有循环"
    assert (Symbol(base="BTC", quote="USDT"), "1m") in loop.added_subscriptions


def test_hot_add_ws_new_symbol_adds_stream_and_task(
    runtime_factory: RuntimeFactory,
) -> None:
    state = [_record("ws_one", DataSource.WS)]
    ws_loops: list[RecordingWsLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        ws_loops=ws_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()

    state.append(_record("ws_two", DataSource.WS, symbol="btc/usdt"))
    runtime.sync_configs_once()

    assert len(ws_loops) == 1
    loop = ws_loops[0]
    assert len(loop.tasks) == 2
    assert loop.added_streams == ["btcusdt@kline_1m"], (
        "新 symbol 必须加入 WS 订阅并触发重连"
    )


def test_hot_update_applies_tuning_parameters(
    runtime_factory: RuntimeFactory,
) -> None:
    state = [_record("hot_update", DataSource.TIMER, grid_spacing_rate=0.01)]
    exchange_calls: list[bool] = []
    timer_loops: list[RecordingTimerLoop] = []

    def exchange_factory() -> Exchange:
        exchange_calls.append(True)
        return _UnusedExchange()

    runtime = runtime_factory(
        [],
        exchange_factory=exchange_factory,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()
    loop = timer_loops[0]
    strategy = _strategy_of(loop.tasks[0])
    assert strategy.config.grid_spacing_rate == pytest.approx(0.01)

    state[0] = _record(
        "hot_update", DataSource.TIMER, grid_spacing_rate=0.05, version=2
    )
    runtime.sync_configs_once()

    assert strategy.config.grid_spacing_rate == pytest.approx(0.05), (
        "调参变更必须热应用到运行中的策略"
    )
    assert len(loop.tasks) == 1, "调参热更新不得重建任务"
    assert len(exchange_calls) == 1, "调参热更新不得重新构造交易所"


def test_hot_update_identity_change_requires_restart(
    runtime_factory: RuntimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    state = [_record("identity", DataSource.TIMER)]
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()
    strategy = _strategy_of(timer_loops[0].tasks[0])

    state[0] = _record("identity", DataSource.TIMER, symbol="btc/usdt", version=2)
    with caplog.at_level(logging.WARNING):
        runtime.sync_configs_once()

    assert strategy.config.symbol == Symbol(base="DOGE", quote="USDT"), (
        "身份字段变更不得热应用"
    )
    assert "需重启" in caplog.text
    assert len(timer_loops) == 1
    assert len(timer_loops[0].tasks) == 1

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        runtime.sync_configs_once()
    assert "需重启" not in caplog.text, "同一版本不得重复告警"


def test_hot_enable_previously_disabled_config_starts_it(
    runtime_factory: RuntimeFactory,
) -> None:
    """启用未运行的配置等价于热新增, 不需要重启"""
    state = [_record("wake_me", DataSource.TIMER, enabled=False)]
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()
    assert timer_loops == [], "停用配置启动时不得装配任务"

    state[0] = _record("wake_me", DataSource.TIMER, enabled=True, version=2)
    runtime.sync_configs_once()

    assert len(timer_loops) == 1, "启用未运行配置应自动热加载"
    assert [task.name for task in timer_loops[0].tasks] == [
        "StrategyTask(DOGE/USDT|1m)"
    ]


def test_hot_disable_requires_restart_and_keeps_strategy_running(
    runtime_factory: RuntimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    state = [_record("disable_me", DataSource.TIMER)]
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()

    state[0] = _record("disable_me", DataSource.TIMER, enabled=False, version=2)
    with caplog.at_level(logging.WARNING):
        runtime.sync_configs_once()

    assert "停用" in caplog.text
    assert len(timer_loops[0].tasks) == 1, (
        "停用不热摘除任务; 需要重启进程, 避免孤立在途订单"
    )


def test_hot_add_invalid_payload_does_not_stop_runtime(
    runtime_factory: RuntimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    state = [
        StrategyConfigRecord(
            strategy_key="broken", payload="{not json", enabled=True
        )
    ]
    timer_loops: list[RecordingTimerLoop] = []
    runtime = runtime_factory(
        [],
        exchange_factory=_UnusedExchange,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    with caplog.at_level(logging.ERROR):
        runtime.start()

    assert timer_loops == []
    assert "策略热加载失败" in caplog.text
    assert _heartbeat(runtime_factory) is not None, (
        "单条配置失败不得中断运行时, 仍应等待后续配置"
    )


def test_hot_add_disabled_config_is_ignored(
    runtime_factory: RuntimeFactory,
) -> None:
    state = [_record("disabled", DataSource.TIMER, enabled=False)]
    exchange_calls: list[bool] = []
    timer_loops: list[RecordingTimerLoop] = []

    def exchange_factory() -> Exchange:
        exchange_calls.append(True)
        return _UnusedExchange()

    runtime = runtime_factory(
        [],
        exchange_factory=exchange_factory,
        timer_loops=timer_loops,
        configs_provider=lambda: list(state),
    )
    runtime.start()

    assert exchange_calls == []
    assert timer_loops == []
