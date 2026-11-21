"""配置加载器: 按 data_source / enabled 过滤并装配 StrategyTask"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.exchange.ports import Exchange
from smart_trader.model import (
    DataSource,
    Kline,
    OrderRequest,
    OrderResult,
    Position,
    StrategyLifecycle,
    StrategySignalType,
    Symbol,
    SymbolInfo,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_loader import build_strategy_tasks
from smart_trader.strategy.config_payload import StrategyConfigPayload
from smart_trader.strategy.inverted_signal import InvertedSignal
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from smart_trader.task.strategy_task import StrategyTask


class _UnusedExchange(Exchange):
    """只满足端口签名; 加载期不应触达行情 / 交易 / 账户"""

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        raise AssertionError("加载期不得查询交易规则")

    def latest_price(self, symbol: Symbol) -> float:
        raise AssertionError("加载期不得查询行情")

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        raise AssertionError("加载期不得拉取K线")

    def place(self, request: OrderRequest) -> OrderResult:
        raise AssertionError("加载期不得下单")

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return None

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return None

    def balance(self, coin: str) -> float:
        return 0.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        return []


def _record(
    strategy_key: str,
    data_source: DataSource,
    *,
    enabled: bool = True,
    max_order: int = 5,
    signal_type: StrategySignalType = StrategySignalType.ALPHA_TREND,
    desired_running: bool = True,
    lifecycle: StrategyLifecycle = StrategyLifecycle.ACTIVE,
) -> StrategyConfigRecord:
    payload = StrategyConfigPayload(
        symbol="doge/usdt",
        timeframe="1m",
        data_source=data_source,
        max_order=max_order,
        signal_type=signal_type,
    )
    return StrategyConfigRecord(
        strategy_key=strategy_key,
        payload=payload.to_json(),
        enabled=enabled,
        desired_running=desired_running,
        lifecycle=lifecycle,
    )


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(tmp_path / "loader.db"))
    yield instance
    instance.close()


@pytest.fixture
def exchange() -> Exchange:
    return _UnusedExchange()


def _strategy_of(task: StrategyTask) -> SignalGridStrategy:
    """断言加载器装配的是 SignalGridStrategy, 供 pyright 收窄类型"""
    strategy = task.strategy
    assert isinstance(strategy, SignalGridStrategy), (
        f"加载器必须装配 SignalGridStrategy, 实际 {type(strategy).__name__}"
    )
    return strategy


def test_builds_only_enabled_matching_source(journal: OrderJournal, exchange: Exchange):
    records = [
        _record("timer_enabled", DataSource.TIMER),
        _record("ws_enabled", DataSource.WS),
        _record("timer_disabled", DataSource.TIMER, enabled=False),
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert len(tasks) == 1, "只应加载启用且数据源匹配的配置"
    task = tasks[0]
    strategy = _strategy_of(task)
    assert strategy.config.strategy_key == "timer_enabled"
    assert task.symbol == Symbol(base="DOGE", quote="USDT")
    assert task.timeframes == ["1m"]
    assert strategy.config.max_order == 5


def test_ws_source_loads_ws_records(journal: OrderJournal, exchange: Exchange):
    records = [
        _record("timer_enabled", DataSource.TIMER),
        _record("ws_enabled", DataSource.WS),
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.WS, exchange=exchange, journal=journal
    )

    assert [_strategy_of(task).config.strategy_key for task in tasks] == [
        "ws_enabled"
    ]


def test_guard_config_is_loaded_paused(journal: OrderJournal, exchange: Exchange):
    records = [
        _record(
            "guard_me",
            DataSource.TIMER,
            enabled=False,
            lifecycle=StrategyLifecycle.GUARD,
            desired_running=False,
        )
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert len(tasks) == 1, "守护态配置重启后必须加载, 否则仓位无人跟踪"
    strategy = _strategy_of(tasks[0])
    assert strategy.is_running is False, "守护态必须以暂停启动"


def test_disabling_config_is_loaded_paused(journal: OrderJournal, exchange: Exchange):
    records = [
        _record(
            "disabling_me",
            DataSource.TIMER,
            enabled=False,
            lifecycle=StrategyLifecycle.DISABLING,
            desired_running=False,
        )
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert len(tasks) == 1
    assert _strategy_of(tasks[0]).is_running is False


def test_disabled_config_is_not_loaded(journal: OrderJournal, exchange: Exchange):
    records = [
        _record(
            "disabled_me",
            DataSource.TIMER,
            enabled=False,
            lifecycle=StrategyLifecycle.DISABLED,
            desired_running=False,
        )
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert tasks == []


def test_paused_active_config_starts_paused(journal: OrderJournal, exchange: Exchange):
    records = [
        _record(
            "paused_me",
            DataSource.TIMER,
            desired_running=False,
        )
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert len(tasks) == 1
    assert _strategy_of(tasks[0]).is_running is False, (
        "暂停意图必须跨重启保留"
    )

def test_task_order_follows_input_order(journal: OrderJournal, exchange: Exchange):
    records = [
        _record("b_strategy", DataSource.TIMER),
        _record("a_strategy", DataSource.TIMER),
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    assert [_strategy_of(task).config.strategy_key for task in tasks] == [
        "b_strategy",
        "a_strategy",
    ], "加载顺序必须与输入一致, 便于复现"


def test_loads_inverted_alpha_trend_signal(
    journal: OrderJournal, exchange: Exchange
):
    records = [
        _record(
            "inverse_strategy",
            DataSource.TIMER,
            signal_type=StrategySignalType.ALPHA_TREND_INVERSE,
        )
    ]

    tasks = build_strategy_tasks(
        records, data_source=DataSource.TIMER, exchange=exchange, journal=journal
    )

    config = _strategy_of(tasks[0]).config
    assert isinstance(config.signal, InvertedSignal), (
        f"应装配 InvertedSignal, 实际 {type(config.signal).__name__}"
    )


def test_empty_selection_returns_empty_list(
    journal: OrderJournal, exchange: Exchange
):
    """统一入口允许只启动控制台: 空集合交给运行时决定, 不再在加载器里抛错"""
    records = [_record("timer_disabled", DataSource.TIMER, enabled=False)]

    tasks = build_strategy_tasks(
        records,
        data_source=DataSource.TIMER,
        exchange=exchange,
        journal=journal,
    )

    assert tasks == [], "没有启用且数据源匹配的配置时应返回空列表"


def test_invalid_payload_names_the_offending_strategy(
    journal: OrderJournal, exchange: Exchange
):
    records = [StrategyConfigRecord(strategy_key="broken", payload="{not json")]

    with pytest.raises(ValueError, match="broken"):
        build_strategy_tasks(
            records,
            data_source=DataSource.TIMER,
            exchange=exchange,
            journal=journal,
        )
