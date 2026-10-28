"""CommandRunner 测试: 状态流转 / 未知策略 / 异常隔离 / kill switch"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import (
    CommandAction,
    CommandStatus,
    PositionSide,
    StrategyRuntime,
)
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import CommandRecord
from smart_trader.strategy.control import ControllableStrategy
from smart_trader.task.command_runner import (
    CommandRunner,
    controllable_strategies,
)
from smart_trader.task.strategy_task import StrategyTask
from tests.fakes import DEFAULT_SYMBOL
from tests.test_signal_grid_strategy import build_strategy

KEY = "dogeusdt_long_buy_1m"
OTHER_KEY = "zecusdt_short_sell_1m"


class FakeControllableStrategy(ControllableStrategy):
    """记录控制调用的内存策略"""

    def __init__(
        self,
        key: str,
        *,
        fail_pause: bool = False,
        fail_resume: bool = False,
        fail_close: bool = False,
        fail_cancel: bool = False,
    ):
        self._key = key
        self.fail_pause = fail_pause
        self.fail_resume = fail_resume
        self.fail_close = fail_close
        self.fail_cancel = fail_cancel
        self.paused = False
        self.close_requests = 0
        self.cancel_calls = 0

    @property
    def strategy_key(self) -> str:
        return self._key

    def runtime_info(self) -> StrategyRuntime:
        return StrategyRuntime(
            strategy_key=self._key,
            symbol=DEFAULT_SYMBOL.ccxt(),
            position_side=PositionSide.LONG,
            timeframe="1m",
            is_running=not self.paused,
        )

    def pause(self) -> None:
        if self.fail_pause:
            raise RuntimeError("pause boom")
        self.paused = True

    def resume(self) -> None:
        if self.fail_resume:
            raise RuntimeError("resume boom")
        self.paused = False

    def request_close_all(self) -> None:
        if self.fail_close:
            raise RuntimeError("close boom")
        self.close_requests += 1

    def cancel_pending_entries(self) -> tuple[int, int]:
        if self.fail_cancel:
            raise RuntimeError("cancel boom")
        self.cancel_calls += 1
        return (3, 1)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[CommandStore]:
    journal = OrderJournal(str(db_path))
    journal.close()
    instance = CommandStore(str(db_path))
    yield instance
    instance.close()


def _runner(
    store: CommandStore, *strategies: FakeControllableStrategy
) -> CommandRunner:
    return CommandRunner(
        store, {strategy.strategy_key: strategy for strategy in strategies}
    )


def _command(store: CommandStore, command_id: str) -> CommandRecord:
    command = store.get_command(command_id)
    assert command is not None
    return command


def test_run_once_pause_and_resume(store: CommandStore):
    strategy = FakeControllableStrategy(KEY)
    runner = _runner(store, strategy)
    pause = store.submit(action=CommandAction.PAUSE, strategy_key=KEY)
    resume = store.submit(action=CommandAction.RESUME, strategy_key=KEY)

    assert runner.run_once() is True
    assert strategy.paused is True
    assert _command(store, pause.id).status is CommandStatus.SUCCEEDED

    assert runner.run_once() is True
    assert strategy.paused is False
    assert _command(store, resume.id).status is CommandStatus.SUCCEEDED


def test_run_once_close_all(store: CommandStore):
    strategy = FakeControllableStrategy(KEY)
    runner = _runner(store, strategy)
    command = store.submit(action=CommandAction.CLOSE_ALL, strategy_key=KEY)

    runner.run_once()

    assert strategy.close_requests == 1
    loaded = _command(store, command.id)
    assert loaded.status is CommandStatus.SUCCEEDED
    assert loaded.result is not None and "下一根" in loaded.result


def test_run_once_cancel_entries_reports_counts(store: CommandStore):
    strategy = FakeControllableStrategy(KEY)
    runner = _runner(store, strategy)
    command = store.submit(action=CommandAction.CANCEL_ENTRIES, strategy_key=KEY)

    runner.run_once()

    assert strategy.cancel_calls == 1
    loaded = _command(store, command.id)
    assert loaded.status is CommandStatus.SUCCEEDED
    assert loaded.result is not None
    assert "3" in loaded.result and "1" in loaded.result


def test_run_once_unknown_strategy_marks_failed(store: CommandStore):
    runner = _runner(store)
    command = store.submit(action=CommandAction.PAUSE, strategy_key=KEY)

    runner.run_once()

    loaded = _command(store, command.id)
    assert loaded.status is CommandStatus.FAILED
    assert loaded.result is not None and "策略未运行" in loaded.result


def test_run_once_survives_exception_and_continues(store: CommandStore):
    strategy = FakeControllableStrategy(KEY, fail_pause=True)
    runner = _runner(store, strategy)
    pause = store.submit(action=CommandAction.PAUSE, strategy_key=KEY)
    resume = store.submit(action=CommandAction.RESUME, strategy_key=KEY)

    assert runner.run_once() is True
    assert _command(store, pause.id).status is CommandStatus.FAILED

    assert runner.run_once() is True
    assert _command(store, resume.id).status is CommandStatus.SUCCEEDED
    assert strategy.paused is False


def test_kill_switch_applies_to_all_strategies(store: CommandStore):
    first = FakeControllableStrategy(KEY)
    second = FakeControllableStrategy(OTHER_KEY)
    runner = _runner(store, first, second)
    command = store.submit(action=CommandAction.KILL_SWITCH, strategy_key=None)

    runner.run_once()

    assert first.paused is True and first.close_requests == 1
    assert second.paused is True and second.close_requests == 1
    loaded = _command(store, command.id)
    assert loaded.status is CommandStatus.SUCCEEDED
    assert loaded.result is not None and "2" in loaded.result


def test_kill_switch_partial_failure_marks_failed(store: CommandStore):
    failing = FakeControllableStrategy(KEY, fail_close=True)
    healthy = FakeControllableStrategy(OTHER_KEY)
    runner = _runner(store, failing, healthy)
    command = store.submit(action=CommandAction.KILL_SWITCH, strategy_key=None)

    runner.run_once()

    assert healthy.paused is True, "单策略失败不得阻止其它策略暂停"
    assert healthy.close_requests == 1
    loaded = _command(store, command.id)
    assert loaded.status is CommandStatus.FAILED
    assert loaded.result is not None and KEY in loaded.result


def test_run_once_returns_false_without_pending(store: CommandStore):
    runner = _runner(store, FakeControllableStrategy(KEY))
    assert runner.run_once() is False


def test_controllable_strategies_extracts_grid_tasks():
    strategy, _, _, _ = build_strategy()
    task = StrategyTask(symbol=DEFAULT_SYMBOL, strategy=strategy)

    assert controllable_strategies([task]) == {strategy.strategy_key: strategy}
