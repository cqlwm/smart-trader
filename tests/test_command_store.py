"""CommandStore 测试: 提交 / 幂等 / 原子认领 / 完成 / 查询 / 缺表提示"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import CommandAction, CommandStatus
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from tests.test_storage_schema import create_pre_reason_database

STRATEGY_KEY = "dogeusdt_long_buy_1m"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def store(db_path: Path, journal: OrderJournal) -> Iterator[CommandStore]:
    instance = CommandStore(str(db_path))
    yield instance
    instance.close()


def test_submit_persists_pending_command(store: CommandStore):
    command = store.submit(
        action=CommandAction.PAUSE,
        strategy_key=STRATEGY_KEY,
        requested_by="web",
    )

    assert command.id.startswith("CMD"), "命令号必须可识别"
    assert command.status is CommandStatus.PENDING
    assert command.requested_at
    assert command.started_at is None
    assert command.finished_at is None


def test_submit_same_idempotency_key_returns_existing(store: CommandStore):
    first = store.submit(
        action=CommandAction.PAUSE,
        strategy_key=STRATEGY_KEY,
        idempotency_key="ik-1",
    )
    second = store.submit(
        action=CommandAction.RESUME,
        strategy_key=STRATEGY_KEY,
        idempotency_key="ik-1",
    )

    assert second.id == first.id, "同一幂等键必须返回已有命令"
    assert second.action is CommandAction.PAUSE, "已有命令保持不变"
    assert store.count_commands() == 1


def test_submit_kill_switch_must_be_global(store: CommandStore):
    with pytest.raises(ValueError, match="kill_switch"):
        store.submit(
            action=CommandAction.KILL_SWITCH, strategy_key=STRATEGY_KEY
        )

    global_command = store.submit(
        action=CommandAction.KILL_SWITCH, strategy_key=None
    )
    assert global_command.strategy_key is None


def test_submit_strategy_action_requires_strategy_key(store: CommandStore):
    with pytest.raises(ValueError, match="strategy_key"):
        store.submit(action=CommandAction.PAUSE, strategy_key=None)


def test_claim_next_marks_running_with_started_at(store: CommandStore):
    submitted = store.submit(
        action=CommandAction.PAUSE, strategy_key=STRATEGY_KEY
    )

    claimed = store.claim_next()

    assert claimed is not None
    assert claimed.id == submitted.id
    assert claimed.status is CommandStatus.RUNNING
    assert claimed.started_at is not None
    assert store.claim_next() is None, "同一命令不得被认领两次"


def test_claim_next_returns_none_without_pending(store: CommandStore):
    assert store.claim_next() is None


def test_claim_next_follows_submission_order(store: CommandStore):
    first = store.submit(action=CommandAction.PAUSE, strategy_key=STRATEGY_KEY)
    second = store.submit(action=CommandAction.RESUME, strategy_key=STRATEGY_KEY)

    claimed_first = store.claim_next()
    claimed_second = store.claim_next()

    assert claimed_first is not None and claimed_first.id == first.id
    assert claimed_second is not None and claimed_second.id == second.id


def test_complete_marks_succeeded_with_result(store: CommandStore):
    command = store.submit(
        action=CommandAction.PAUSE, strategy_key=STRATEGY_KEY
    )
    store.claim_next()

    store.complete(command.id, status=CommandStatus.SUCCEEDED, result="paused")

    loaded = store.get_command(command.id)
    assert loaded is not None
    assert loaded.status is CommandStatus.SUCCEEDED
    assert loaded.result == "paused"
    assert loaded.finished_at is not None


def test_complete_rejects_non_terminal_status(store: CommandStore):
    command = store.submit(
        action=CommandAction.PAUSE, strategy_key=STRATEGY_KEY
    )
    store.claim_next()

    with pytest.raises(ValueError, match="终态"):
        store.complete(command.id, status=CommandStatus.RUNNING)


def test_complete_rejects_command_not_running(store: CommandStore):
    command = store.submit(
        action=CommandAction.PAUSE, strategy_key=STRATEGY_KEY
    )

    with pytest.raises(ValueError, match="running"):
        store.complete(command.id, status=CommandStatus.SUCCEEDED)


def test_list_commands_filters_by_strategy_and_paginates(store: CommandStore):
    for index in range(3):
        store.submit(
            action=CommandAction.PAUSE,
            strategy_key=STRATEGY_KEY,
            idempotency_key=f"a-{index}",
        )
    store.submit(
        action=CommandAction.PAUSE,
        strategy_key="other_strategy",
        idempotency_key="other",
    )

    page = store.list_commands(strategy_key=STRATEGY_KEY, limit=2, offset=0)
    total = store.count_commands(strategy_key=STRATEGY_KEY)

    assert len(page) == 2
    assert total == 3
    assert all(item.strategy_key == STRATEGY_KEY for item in page)
    assert store.list_commands(limit=10, offset=0) == store.list_commands(
        limit=10, offset=0
    ), "查询必须稳定幂等"


def test_missing_commands_table_raises_migration_hint(tmp_path: Path):
    path = tmp_path / "legacy.db"
    create_pre_reason_database(str(path))

    with pytest.raises(ValueError, match="002_add_commands"):
        CommandStore(str(path))
