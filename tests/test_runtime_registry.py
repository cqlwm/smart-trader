"""运行态心跳文件测试: 写入 / 暂停状态 / 清理 / 损坏容忍"""

from pathlib import Path

from smart_trader.model import ReconcileSnapshot
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.task.command_runner import CommandRunner
from tests.test_command_runner import FakeControllableStrategy

KEY = "dogeusdt_long_buy_1m"


def test_heartbeat_writes_snapshot(tmp_path: Path):
    registry = RuntimeRegistry(tmp_path / "runtime.json")

    registry.heartbeat([FakeControllableStrategy(KEY)])

    snapshot = registry.read()
    assert snapshot is not None
    assert snapshot.strategies[0].strategy_key == KEY
    assert snapshot.strategies[0].is_running is True
    assert snapshot.strategies[0].heartbeat_at, "必须带心跳时间戳"


def test_heartbeat_roundtrips_reconcile_snapshot(tmp_path: Path):
    reconcile = ReconcileSnapshot(
        checked_at="2026-10-29T00:00:00+00:00",
        exchange_qty=3500.0,
        local_qty=500.0,
        gap=-3000.0,
        tracked_exit_orders=1,
        max_exit_query_failures=3,
        failing_exit_orders=["exit1"],
    )
    registry = RuntimeRegistry(tmp_path / "runtime.json")

    registry.heartbeat([FakeControllableStrategy(KEY, reconcile=reconcile)])

    snapshot = registry.read()
    assert snapshot is not None
    loaded = snapshot.strategies[0].reconcile
    assert loaded is not None
    assert loaded.gap == -3000.0
    assert loaded.failing_exit_orders == ["exit1"]


def test_heartbeat_reflects_pause(tmp_path: Path):
    strategy = FakeControllableStrategy(KEY)
    registry = RuntimeRegistry(tmp_path / "runtime.json")

    strategy.pause()
    registry.heartbeat([strategy])

    snapshot = registry.read()
    assert snapshot is not None
    assert snapshot.strategies[0].is_running is False


def test_clear_removes_file(tmp_path: Path):
    registry = RuntimeRegistry(tmp_path / "runtime.json")
    registry.heartbeat([FakeControllableStrategy(KEY)])

    registry.clear()

    assert registry.read() is None
    assert not (tmp_path / "runtime.json").exists()


def test_missing_file_returns_none(tmp_path: Path):
    assert RuntimeRegistry(tmp_path / "missing.json").read() is None


def test_corrupt_file_returns_none(tmp_path: Path):
    path = tmp_path / "runtime.json"
    path.write_text("{not-json", encoding="utf-8")

    assert RuntimeRegistry(path).read() is None


def test_runner_heartbeat_and_stop_clear_registry(tmp_path: Path):
    db_path = tmp_path / "smart_trader.db"
    journal = OrderJournal(str(db_path))
    journal.close()
    store = CommandStore(str(db_path))
    strategy = FakeControllableStrategy(KEY)
    registry = RuntimeRegistry(tmp_path / "runtime.json")
    runner = CommandRunner(store, {KEY: strategy}, registry=registry)
    try:
        runner.heartbeat()
        snapshot = registry.read()
        assert snapshot is not None and snapshot.strategies[0].is_running is True

        strategy.pause()
        runner.heartbeat()
        snapshot = registry.read()
        assert snapshot is not None and snapshot.strategies[0].is_running is False

        runner.stop()
        assert registry.read() is None, "正常退出必须清除在线状态"
    finally:
        store.close()
