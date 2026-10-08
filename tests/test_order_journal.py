"""OrderJournal（sqlite 订单日志）测试

三张表: order_ids（WAL/id 分发）、grid_positions（逻辑仓位）、orders（订单流水）。
覆盖: 预留/确认/核销/待对账/仓位同步归档/流水记录/事务原子性/多策略隔离。
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import OrderSide, Symbol
from smart_trader.storage.order_journal import (
    GridPositionRow,
    IdRecord,
    OrderJournal,
    OrderRecord,
    OrderRole,
)
from tests.fakes import (
    make_entry_intent,
    make_exit_intent,
    make_order_record,
    make_position_row,
)

SYMBOL = Symbol(base="DOGE", quote="USDT")
STRATEGY_KEY = "dogeusdt_long_buy_1m"
OTHER_KEY = "qqqusdt_long_buy_1m"


@pytest.fixture
def journal() -> Iterator[OrderJournal]:
    instance = OrderJournal(":memory:")
    yield instance
    instance.close()


class TestReserve:
    def test_reserve_returns_id_and_records_pending_intent(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())

        assert custom_id, "必须返回可用的订单号"
        pending = journal.pending_ids(STRATEGY_KEY)
        assert len(pending) == 1
        record = pending[0]
        assert record.id == custom_id
        assert record.used is False, "刚预留的 id 处于未确认状态"
        assert record.type == "ORDER"
        assert record.strategy_key == STRATEGY_KEY
        assert record.intent.kind is OrderRole.ENTRY, "意图快照必须完整往返"

    def test_reserve_round_trips_position_snapshot(self, journal: OrderJournal):
        journal.reserve(
            STRATEGY_KEY,
            make_entry_intent(make_position_row("e9", price=0.123)),
        )

        record = journal.pending_ids(STRATEGY_KEY)[0]
        assert record.intent.entry_position is not None
        assert record.intent.entry_position.entry_id == "e9"
        assert record.intent.entry_position.price == pytest.approx(0.123)

    def test_reserve_uses_preferred_id(self, journal: OrderJournal):
        custom_id = journal.reserve(
            STRATEGY_KEY, make_entry_intent(), preferred_id="BUYabc123"
        )

        assert custom_id == "BUYabc123"

    def test_reserve_allocates_new_id_when_preferred_conflicts(
        self, journal: OrderJournal
    ):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent(), preferred_id="dup1")
        second = journal.reserve(STRATEGY_KEY, make_entry_intent(), preferred_id="dup1")

        assert first == "dup1"
        assert second != "dup1", "同一订单号绝不允许复用"

    def test_reserve_generated_ids_are_unique(self, journal: OrderJournal):
        ids = [journal.reserve(STRATEGY_KEY, make_entry_intent()) for _ in range(5)]

        assert len(set(ids)) == 5

    def test_pending_ids_isolated_by_strategy_key(self, journal: OrderJournal):
        journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.reserve(OTHER_KEY, make_entry_intent())

        assert len(journal.pending_ids(STRATEGY_KEY)) == 1
        assert len(journal.pending_ids(OTHER_KEY)) == 1
        assert journal.pending_ids("unknown") == []


class TestConfirmEntry:
    def test_writes_position_and_record_and_settles_id(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())

        journal.confirm_entry(
            custom_id, make_position_row("e1"), make_order_record("BUYexchange1")
        )

        positions = journal.load_positions(STRATEGY_KEY)
        assert [row.entry_id for row in positions] == ["e1"]
        records = journal.load_order_records(STRATEGY_KEY)
        assert [record.order_id for record in records] == ["BUYexchange1"]
        assert records[0].role is OrderRole.ENTRY
        assert journal.pending_ids(STRATEGY_KEY) == [], "确认后不应再留在待对账清单"

    def test_rolls_back_everything_when_id_unknown(self, journal: OrderJournal):
        """订单号未登记时 confirm 必须整体失败, 仓位与流水都不能留下"""
        with pytest.raises(ValueError):
            journal.confirm_entry(
                "never-reserved", make_position_row("e1"), make_order_record("x1")
            )

        assert journal.load_positions(STRATEGY_KEY) == []
        assert journal.load_order_records(STRATEGY_KEY) == []

    def test_updates_existing_position(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(custom_id, make_position_row("e1"), make_order_record())

        journal.confirm_entry(
            custom_id,
            make_position_row("e1", filled_quantity=100.0),
            make_order_record(),
        )

        positions = journal.load_positions(STRATEGY_KEY)
        assert len(positions) == 1
        assert positions[0].filled_quantity == pytest.approx(100.0)

    def test_record_upsert_is_idempotent_for_reconcile_rerun(
        self, journal: OrderJournal
    ):
        """同一订单流水重复写入（对账重跑）不产生重复行"""
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(custom_id, make_position_row("e1"), make_order_record())
        second = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(second, make_position_row("e1"), make_order_record())

        assert len(journal.load_order_records(STRATEGY_KEY)) == 1


class TestConfirmExit:
    def test_backfills_positions_and_records_exit_order(self, journal: OrderJournal):
        for entry_id in ("e1", "e2"):
            custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
            journal.confirm_entry(
                custom_id,
                make_position_row(entry_id),
                make_order_record(f"in-{entry_id}"),
            )
        exit_id = journal.reserve(STRATEGY_KEY, make_exit_intent(["e1", "e2"]))

        journal.confirm_exit(
            exit_id,
            ["e1", "e2"],
            exit_id="SELLxyz",
            exit_price=0.55,
            order=make_order_record("SELLxyz", OrderRole.EXIT, side=OrderSide.SELL),
        )

        positions = {row.entry_id: row for row in journal.load_positions(STRATEGY_KEY)}
        assert positions["e1"].exit_id == "SELLxyz"
        assert positions["e1"].exit_price == pytest.approx(0.55)
        assert positions["e2"].exit_id == "SELLxyz"
        roles = [record.role for record in journal.load_order_records(STRATEGY_KEY)]
        assert roles.count(OrderRole.EXIT) == 1
        assert journal.pending_ids(STRATEGY_KEY) == []

    def test_rolls_back_when_id_unknown(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(first, make_position_row("e1"), make_order_record())

        with pytest.raises(ValueError):
            journal.confirm_exit(
                "never-reserved",
                ["e1"],
                exit_id="x",
                exit_price=1.0,
                order=make_order_record("x", OrderRole.EXIT),
            )

        positions = journal.load_positions(STRATEGY_KEY)
        assert positions[0].exit_id is None, "失败回滚后仓位不能带出场信息"
        assert len(journal.load_order_records(STRATEGY_KEY)) == 1


class TestVoid:
    def test_void_settles_id_without_writes(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())

        journal.void(custom_id)

        assert journal.pending_ids(STRATEGY_KEY) == []
        assert journal.load_positions(STRATEGY_KEY) == []

    def test_void_rejects_unknown_id(self, journal: OrderJournal):
        with pytest.raises(ValueError):
            journal.void("never-reserved")


class TestLoadPositions:
    def test_only_returns_active(self, journal: OrderJournal):
        for entry_id in ("e1", "e2"):
            custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
            journal.confirm_entry(
                custom_id, make_position_row(entry_id), make_order_record(f"o-{entry_id}")
            )

        journal.deactivate_position("e2")

        positions = journal.load_positions(STRATEGY_KEY)
        assert [row.entry_id for row in positions] == ["e1"], "已平仓归档的仓位不应再加载"

    def test_isolated_by_strategy_key(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(first, make_position_row("e1"), make_order_record())
        second = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm_entry(
            second,
            make_position_row("e2", strategy_key=OTHER_KEY),
            make_order_record(strategy_key=OTHER_KEY),
        )

        assert [row.entry_id for row in journal.load_positions(STRATEGY_KEY)] == ["e1"]
        assert [row.entry_id for row in journal.load_positions(OTHER_KEY)] == ["e2"]

    def test_preserves_all_fields(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        row = make_position_row(
            "e1",
            filled_quantity=40.0,
            exit_price=0.6,
            exit_id="SELL1",
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            trailing_stop_rate=0.02,
            enable_trailing_stop=True,
            trailing_stop_activation_profit_rate=0.01,
            current_stop_price=0.47,
        )
        journal.confirm_entry(custom_id, row, make_order_record())

        loaded = journal.load_positions(STRATEGY_KEY)[0]
        assert loaded.model_dump(exclude={"created_at", "updated_at"}) == row.model_dump(
            exclude={"created_at", "updated_at"}
        ), "往返后除时间戳外字段必须完全一致"


class TestArchivedPositions:
    def test_returns_deactivated_positions(self, journal: OrderJournal):
        for entry_id in ("e1", "e2"):
            custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
            journal.confirm_entry(
                custom_id, make_position_row(entry_id), make_order_record(f"o-{entry_id}")
            )
        journal.deactivate_position("e1")

        archived = journal.load_archived_positions(STRATEGY_KEY)
        assert [row.entry_id for row in archived] == ["e1"], "归档查询必须能拿到已平仓仓位"

    def test_isolated_by_strategy_key(self, journal: OrderJournal):
        custom_id = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm_entry(
            custom_id,
            make_position_row("other", strategy_key=OTHER_KEY),
            make_order_record(strategy_key=OTHER_KEY),
        )
        journal.deactivate_position("other")

        assert journal.load_archived_positions(STRATEGY_KEY) == []


class TestOrderRecords:
    def test_records_survive_position_archive(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(custom_id, make_position_row("e1"), make_order_record())

        journal.deactivate_position("e1")

        records = journal.load_order_records(STRATEGY_KEY)
        assert [record.order_id for record in records] == ["BUYexchange1"], (
            "仓位归档不影响订单流水"
        )

    def test_sync_positions_does_not_touch_records(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(custom_id, make_position_row("e1"), make_order_record())

        journal.sync_positions(STRATEGY_KEY, [], removed_ids=["e1"])

        assert len(journal.load_order_records(STRATEGY_KEY)) == 1

    def test_isolated_by_strategy_key(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(first, make_position_row("e1"), make_order_record("o1"))
        second = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm_entry(
            second,
            make_position_row("e2", strategy_key=OTHER_KEY),
            make_order_record("o2", strategy_key=OTHER_KEY),
        )

        assert [r.order_id for r in journal.load_order_records(STRATEGY_KEY)] == ["o1"]
        assert [r.order_id for r in journal.load_order_records(OTHER_KEY)] == ["o2"]


class TestSyncPositions:
    def test_upserts_current_and_deactivates_removed(self, journal: OrderJournal):
        for entry_id in ("e1", "e2"):
            custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
            journal.confirm_entry(
                custom_id, make_position_row(entry_id), make_order_record(f"o-{entry_id}")
            )

        journal.sync_positions(
            STRATEGY_KEY,
            [make_position_row("e1", filled_quantity=100.0)],
            removed_ids=["e2"],
        )

        positions = journal.load_positions(STRATEGY_KEY)
        assert [row.entry_id for row in positions] == ["e1"]
        assert positions[0].filled_quantity == pytest.approx(100.0)

    def test_inserts_rows_never_seen_before(self, journal: OrderJournal):
        """兼容旧数据导入: 不经 reserve 直接落表的行也必须可见"""
        journal.sync_positions(
            STRATEGY_KEY, [make_position_row("imported")], removed_ids=[]
        )

        assert [row.entry_id for row in journal.load_positions(STRATEGY_KEY)] == [
            "imported"
        ]

    def test_does_not_touch_other_strategies(self, journal: OrderJournal):
        custom_id = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm_entry(
            custom_id,
            make_position_row("other", strategy_key=OTHER_KEY),
            make_order_record(),
        )

        journal.sync_positions(STRATEGY_KEY, [], removed_ids=["other"])

        assert [row.entry_id for row in journal.load_positions(OTHER_KEY)] == ["other"]


class TestPersistence:
    def test_survive_reopen(self, tmp_path: Path):
        path = str(tmp_path / "orders.db")
        first = OrderJournal(path)
        custom_id = first.reserve(STRATEGY_KEY, make_entry_intent())
        first.confirm_entry(custom_id, make_position_row("e1"), make_order_record())
        first.close()

        second = OrderJournal(path)
        try:
            assert [row.entry_id for row in second.load_positions(STRATEGY_KEY)] == ["e1"]
            assert len(second.load_order_records(STRATEGY_KEY)) == 1
            assert second.pending_ids(STRATEGY_KEY) == []
        finally:
            second.close()


class TestModels:
    def test_id_record_holds_parsed_intent(self):
        record = IdRecord(
            id="cid1",
            used=False,
            type="ORDER",
            strategy_key=STRATEGY_KEY,
            intent=make_entry_intent(),
            created_at="2026-10-07T12:00:00+00:00",
        )

        assert record.intent.kind is OrderRole.ENTRY

    def test_position_row_forbids_extra_fields(self):
        with pytest.raises(ValueError):
            GridPositionRow.model_validate(
                {**make_position_row().model_dump(), "unknown": 1}
            )

    def test_order_record_requires_status(self):
        with pytest.raises(ValueError):
            OrderRecord.model_validate(
                {
                    "order_id": "o1",
                    "strategy_key": STRATEGY_KEY,
                    "role": OrderRole.ENTRY,
                    "symbol": SYMBOL.ccxt(),
                    "side": OrderSide.BUY,
                }
            )


_LEGACY_ORDERS_DDL = """
CREATE TABLE orders (
    entry_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    filled_quantity REAL NOT NULL DEFAULT 0,
    fixed_take_profit_rate REAL NOT NULL,
    signal_min_take_profit_rate REAL NOT NULL,
    exit_price REAL,
    status TEXT,
    exit_id TEXT,
    stop_loss_rate REAL NOT NULL DEFAULT 0,
    enable_stop_loss INTEGER NOT NULL DEFAULT 0,
    trailing_stop_rate REAL NOT NULL DEFAULT 0,
    enable_trailing_stop INTEGER NOT NULL DEFAULT 0,
    trailing_stop_activation_profit_rate REAL NOT NULL DEFAULT 0,
    current_stop_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_LEGACY_ORDER_IDS_DDL = """
CREATE TABLE order_ids (
    id TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL DEFAULT 'ORDER',
    strategy_key TEXT NOT NULL,
    intent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class TestLegacyMigration:
    """旧库（Step 8 之前, orders 表曾是逻辑仓位表）的自动迁移"""

    def test_migrates_legacy_position_orders_table(self, tmp_path: Path):
        path = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(path)
        legacy.executescript(_LEGACY_ORDERS_DDL)
        legacy.execute(
            "INSERT INTO orders VALUES"
            " ('e1','k1','buy',0.5,100,0,0.01,0.002,NULL,'open',NULL,0,0,0,0,0,NULL,1,'t1','t1')"
        )
        legacy.commit()
        legacy.close()

        journal = OrderJournal(path)
        try:
            positions = journal.load_positions("k1")
            assert [row.entry_id for row in positions] == ["e1"], (
                "旧库的逻辑仓位必须完整迁移到 grid_positions"
            )
            assert positions[0].fixed_take_profit_rate == pytest.approx(0.01)
            assert journal.load_order_records("k1") == [], "新流水表必须可用"
        finally:
            journal.close()

    def test_migrates_legacy_intent_snapshot(self, tmp_path: Path):
        """旧意图快照的 entry_order 字段迁移为 entry_position, 仍可解析"""
        path = str(tmp_path / "legacy-intent.db")
        legacy = sqlite3.connect(path)
        legacy.executescript(_LEGACY_ORDERS_DDL)
        legacy.executescript(_LEGACY_ORDER_IDS_DDL)
        legacy_intent = json.dumps(
            {
                "kind": "entry",
                "symbol": {"base": "DOGE", "quote": "USDT"},
                "side": "buy",
                "entry_order": {
                    "entry_id": "e1",
                    "strategy_key": "k1",
                    "side": "buy",
                    "price": 0.5,
                    "quantity": 100.0,
                    "filled_quantity": 0.0,
                    "fixed_take_profit_rate": 0.01,
                    "signal_min_take_profit_rate": 0.002,
                },
            }
        )
        legacy.execute(
            "INSERT INTO order_ids"
            " (id, used, type, strategy_key, intent, created_at, updated_at)"
            " VALUES (?, 0, 'ORDER', 'k1', ?, 't1', 't1')",
            ("cid1", legacy_intent),
        )
        legacy.commit()
        legacy.close()

        journal = OrderJournal(path)
        try:
            pending = journal.pending_ids("k1")
            assert len(pending) == 1, "迁移后旧待对账记录必须仍可解析"
            entry_position = pending[0].intent.entry_position
            assert entry_position is not None
            assert entry_position.entry_id == "e1"
        finally:
            journal.close()

    def test_fresh_database_skips_migration(self, tmp_path: Path):
        path = str(tmp_path / "fresh.db")
        journal = OrderJournal(path)
        try:
            assert journal.load_positions("k1") == []
            assert journal.load_order_records("k1") == []
        finally:
            journal.close()
