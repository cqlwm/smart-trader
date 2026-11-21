"""OrderJournalReader（只读账本）测试: 不建库 / 结构校验 / 只读查询"""

import sqlite3
from pathlib import Path

import pytest

from smart_trader.model import PositionCloseReason
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from tests.api_harness import make_order, make_position, seed_position
from tests.test_storage_schema import create_pre_reason_database


def test_reader_rejects_missing_database(tmp_path: Path):
    path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        OrderJournalReader(str(path))

    assert not path.exists(), "只读仓储不得顺手建库"


def test_reader_rejects_outdated_schema(tmp_path: Path):
    path = tmp_path / "legacy.db"
    create_pre_reason_database(str(path), with_position=True)

    with pytest.raises(ValueError, match="closed_reason"):
        OrderJournalReader(str(path))


def test_reader_loads_positions_and_orders(tmp_path: Path):
    path = tmp_path / "ledger.db"
    journal = OrderJournal(str(path))
    try:
        seed_position(
            journal,
            make_position("POS1"),
            [make_order("buy1", "POS1")],
        )
    finally:
        journal.close()

    reader = OrderJournalReader(str(path))
    try:
        positions = reader.load_positions()
        orders = reader.load_orders_for_positions(["POS1"])
    finally:
        reader.close()

    assert [row.id for row in positions] == ["POS1"]
    assert [order.order_id for order in orders] == ["buy1"]


def test_reader_paginates_archived_positions_newest_first(tmp_path: Path):
    path = tmp_path / "ledger.db"
    journal = OrderJournal(str(path))
    try:
        for position_id in ("POS1", "POS2", "POS3"):
            seed_position(
                journal,
                make_position(position_id),
                [make_order(f"buy-{position_id}", position_id)],
            )
            journal.close_position(position_id, PositionCloseReason.EXIT_SETTLED)
        seed_position(
            journal,
            make_position("POS4"),
            [make_order("buy-POS4", "POS4")],
        )
    finally:
        journal.close()

    # 显式写入更新时间, 避免依赖归档调用的时钟精度
    timestamps = {
        "POS1": "2026-01-01T00:00:00+00:00",
        "POS2": "2026-01-03T00:00:00+00:00",
        "POS3": "2026-01-02T00:00:00+00:00",
    }
    with sqlite3.connect(path) as conn:
        for position_id, timestamp in timestamps.items():
            conn.execute(
                "UPDATE grid_positions SET updated_at = ? WHERE id = ?",
                (timestamp, position_id),
            )

    reader = OrderJournalReader(str(path))
    try:
        first_page = reader.load_archived_positions(limit=2, offset=0)
        second_page = reader.load_archived_positions(limit=2, offset=2)
        total = reader.count_archived_positions()
    finally:
        reader.close()

    assert [row.id for row in first_page] == ["POS2", "POS3"], "归档按更新时间倒序"
    assert [row.id for row in second_page] == ["POS1"], "活跃仓位不得混入归档"
    assert total == 3, f"归档总数应为 3, 实际 {total}"
