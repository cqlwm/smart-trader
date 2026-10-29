"""PositionAckStore: 人工仓位声明 / 替换 / 撤销 / 缺表提示"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import PositionAckStatus, PositionSide
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.position_ack_store import (
    PositionAckNotFoundError,
    PositionAckStore,
)
from smart_trader.storage.records import PositionAckRecord
from tests.test_storage_schema import create_pre_reason_database

KEY = "signal_grid_short_sell_dogeusdt_1m"
OTHER_KEY = "signal_grid_long_buy_dogeusdt_1m"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def store(db_path: Path, journal: OrderJournal) -> Iterator[PositionAckStore]:
    instance = PositionAckStore(str(db_path))
    yield instance
    instance.close()


def _create(
    store: PositionAckStore,
    strategy_key: str = KEY,
    *,
    external_qty: float = 3000.0,
    note: str = "手工中线仓",
) -> PositionAckRecord:
    return store.create(
        strategy_key=strategy_key,
        symbol="DOGE/USDT",
        position_side=PositionSide.SHORT,
        external_qty=external_qty,
        note=note,
    )


def test_create_persists_active_ack(store: PositionAckStore):
    created = _create(store)

    assert created.id.startswith("ACK"), "声明号必须可识别"
    assert created.status is PositionAckStatus.ACTIVE
    assert created.external_qty == 3000.0
    assert created.note == "手工中线仓"
    assert created.symbol == "DOGE/USDT"
    assert created.position_side is PositionSide.SHORT
    assert created.acknowledged_at and created.updated_at
    assert store.active_for(KEY) == created


def test_create_replaces_previous_active_ack(store: PositionAckStore):
    first = _create(store)
    second = _create(store, external_qty=2500.0, note="缩减后的手工仓")

    active = store.active_for(KEY)
    assert active is not None and active.id == second.id
    assert active.external_qty == 2500.0

    old = store.get(first.id)
    assert old is not None
    assert old.status is PositionAckStatus.REVOKED, "旧声明必须撤销而不是删除"
    assert old.revoked_at is not None
    assert store.count() == 2, "历史声明必须保留"


def test_active_for_missing_returns_none(store: PositionAckStore):
    assert store.active_for(KEY) is None


@pytest.mark.parametrize("qty", [0.0, -1.0])
def test_create_rejects_non_positive_quantity(store: PositionAckStore, qty: float):
    with pytest.raises(ValueError, match="external_qty"):
        _create(store, external_qty=qty)


def test_revoke_marks_revoked_and_keeps_history(store: PositionAckStore):
    created = _create(store)

    revoked = store.revoke(created.id, revoked_by="ops")

    assert revoked.status is PositionAckStatus.REVOKED
    assert revoked.revoked_by == "ops"
    assert revoked.revoked_at is not None
    assert store.active_for(KEY) is None
    assert store.get(created.id) == revoked


def test_revoke_is_idempotent(store: PositionAckStore):
    created = _create(store)
    store.revoke(created.id)

    again = store.revoke(created.id)

    assert again.status is PositionAckStatus.REVOKED
    assert store.count() == 1


def test_revoke_unknown_raises_not_found(store: PositionAckStore):
    with pytest.raises(PositionAckNotFoundError):
        store.revoke("ACKmissing")


def test_list_returns_newest_first(store: PositionAckStore):
    _create(store, strategy_key=KEY)
    _create(store, strategy_key=OTHER_KEY)

    rows = store.list()

    assert len(rows) == 2
    assert rows[0].strategy_key == OTHER_KEY, "列表按最新声明优先"


def test_missing_table_raises_migration_hint(tmp_path: Path):
    path = tmp_path / "legacy.db"
    create_pre_reason_database(str(path))

    with pytest.raises(ValueError, match="005_add_position_acks"):
        PositionAckStore(str(path))
