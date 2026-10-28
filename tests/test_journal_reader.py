"""OrderJournalReader（只读账本）测试: 不建库 / 结构校验 / 只读查询"""

from pathlib import Path

import pytest

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
