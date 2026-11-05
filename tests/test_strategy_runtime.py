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

from smart_trader.data_event_loop import DataEventLoop
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
from smart_trader.strategy_runtime import StrategyRuntime


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


class RecordingTimerLoop(_RecordingLoop):
    def __init__(
        self, subscriptions: list[tuple[Symbol, str]], market: MarketDataPort
    ) -> None:
        super().__init__()
        self.subscriptions = subscriptions
        self.market = market


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
    strategy_key: str, data_source: DataSource, *, enabled: bool = True
) -> StrategyConfigRecord:
    payload = StrategyConfigPayload(
        symbol="doge/usdt",
        timeframe="1m",
        data_source=data_source,
        max_order=3,
    )
    return StrategyConfigRecord(
        strategy_key=strategy_key, payload=payload.to_json(), enabled=enabled
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
