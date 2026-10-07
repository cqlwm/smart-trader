"""OrderJournal（sqlite 订单日志）测试

覆盖: 预留/确认/核销/待对账/加载/同步, 事务原子性, 多策略隔离。
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import Symbol
from smart_trader.storage.order_journal import (
    IdRecord,
    IntentKind,
    OrderJournal,
)
from tests.fakes import make_entry_intent, make_exit_intent, make_order_row

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
        assert record.intent.kind is IntentKind.ENTRY, "意图快照必须完整往返"

    def test_reserve_round_trips_entry_order_snapshot(self, journal: OrderJournal):
        journal.reserve(STRATEGY_KEY, make_entry_intent(make_order_row("e9", price=0.123)))

        record = journal.pending_ids(STRATEGY_KEY)[0]
        assert record.intent.entry_order is not None
        assert record.intent.entry_order.entry_id == "e9"
        assert record.intent.entry_order.price == pytest.approx(0.123)

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


class TestConfirm:
    def test_confirm_writes_order_and_settles_id(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())

        journal.confirm(custom_id, make_order_row("e1"))

        orders = journal.load_orders(STRATEGY_KEY)
        assert [row.entry_id for row in orders] == ["e1"]
        assert journal.pending_ids(STRATEGY_KEY) == [], "确认后不应再留在待对账清单"

    def test_confirm_rolls_back_when_id_unknown(self, journal: OrderJournal):
        """订单号未在 id 表登记时 confirm 必须失败, 且不能留下孤儿订单行"""
        with pytest.raises(ValueError):
            journal.confirm("never-reserved", make_order_row("e1"))

        assert journal.load_orders(STRATEGY_KEY) == [], (
            "confirm 失败必须整体回滚, 不能只写入一半"
        )

    def test_confirm_updates_existing_row(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(custom_id, make_order_row("e1"))

        journal.confirm(custom_id, make_order_row("e1", filled_quantity=100.0))

        orders = journal.load_orders(STRATEGY_KEY)
        assert len(orders) == 1
        assert orders[0].filled_quantity == pytest.approx(100.0)

    def test_confirm_exit_backfills_entry_rows(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(first, make_order_row("e1"))
        second = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(second, make_order_row("e2"))
        exit_id = journal.reserve(STRATEGY_KEY, make_exit_intent(["e1", "e2"]))

        journal.confirm_exit(exit_id, ["e1", "e2"], exit_id="SELLxyz", exit_price=0.55)

        orders = {row.entry_id: row for row in journal.load_orders(STRATEGY_KEY)}
        assert orders["e1"].exit_id == "SELLxyz"
        assert orders["e1"].exit_price == pytest.approx(0.55)
        assert orders["e2"].exit_id == "SELLxyz"
        assert journal.pending_ids(STRATEGY_KEY) == []

    def test_confirm_exit_rolls_back_when_id_unknown(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(first, make_order_row("e1"))

        with pytest.raises(ValueError):
            journal.confirm_exit("never-reserved", ["e1"], exit_id="x", exit_price=1.0)

        orders = journal.load_orders(STRATEGY_KEY)
        assert orders[0].exit_id is None, "失败回滚后入场行不能带出场信息"


class TestVoid:
    def test_void_settles_id_without_order(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())

        journal.void(custom_id)

        assert journal.pending_ids(STRATEGY_KEY) == []
        assert journal.load_orders(STRATEGY_KEY) == []

    def test_void_rejects_unknown_id(self, journal: OrderJournal):
        with pytest.raises(ValueError):
            journal.void("never-reserved")


class TestLoadOrders:
    def test_load_orders_only_returns_active(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(custom_id, make_order_row("e1"))
        second = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(second, make_order_row("e2"))

        journal.deactivate_order("e2")

        orders = journal.load_orders(STRATEGY_KEY)
        assert [row.entry_id for row in orders] == ["e1"], "已平仓归档的订单不应再加载"

    def test_load_orders_isolated_by_strategy_key(self, journal: OrderJournal):
        first = journal.reserve(STRATEGY_KEY, make_entry_intent())
        journal.confirm(first, make_order_row("e1", strategy_key=STRATEGY_KEY))
        second = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm(second, make_order_row("e2", strategy_key=OTHER_KEY))

        assert [row.entry_id for row in journal.load_orders(STRATEGY_KEY)] == ["e1"]
        assert [row.entry_id for row in journal.load_orders(OTHER_KEY)] == ["e2"]

    def test_load_orders_preserves_all_fields(self, journal: OrderJournal):
        custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
        row = make_order_row(
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
        journal.confirm(custom_id, row)

        loaded = journal.load_orders(STRATEGY_KEY)[0]
        assert loaded.model_dump(exclude={"created_at", "updated_at"}) == row.model_dump(
            exclude={"created_at", "updated_at"}
        ), "往返后除时间戳外字段必须完全一致"


class TestSyncOrders:
    def test_sync_upserts_current_and_deactivates_removed(self, journal: OrderJournal):
        for entry_id in ("e1", "e2"):
            custom_id = journal.reserve(STRATEGY_KEY, make_entry_intent())
            journal.confirm(custom_id, make_order_row(entry_id))

        journal.sync_orders(
            STRATEGY_KEY,
            [make_order_row("e1", filled_quantity=100.0)],
            removed_ids=["e2"],
        )

        orders = journal.load_orders(STRATEGY_KEY)
        assert [row.entry_id for row in orders] == ["e1"]
        assert orders[0].filled_quantity == pytest.approx(100.0)

    def test_sync_inserts_rows_never_seen_before(self, journal: OrderJournal):
        """兼容旧数据导入: 不经 reserve 直接落表的行也必须可见"""
        journal.sync_orders(STRATEGY_KEY, [make_order_row("imported")], removed_ids=[])

        assert [row.entry_id for row in journal.load_orders(STRATEGY_KEY)] == [
            "imported"
        ]

    def test_sync_does_not_touch_other_strategies(self, journal: OrderJournal):
        custom_id = journal.reserve(OTHER_KEY, make_entry_intent())
        journal.confirm(custom_id, make_order_row("other", strategy_key=OTHER_KEY))

        journal.sync_orders(STRATEGY_KEY, [], removed_ids=["other"])

        assert [row.entry_id for row in journal.load_orders(OTHER_KEY)] == ["other"]


class TestPersistence:
    def test_orders_survive_reopen(self, tmp_path: Path):
        path = str(tmp_path / "orders.db")
        first = OrderJournal(path)
        custom_id = first.reserve(STRATEGY_KEY, make_entry_intent())
        first.confirm(custom_id, make_order_row("e1"))
        first.close()

        second = OrderJournal(path)
        try:
            orders = second.load_orders(STRATEGY_KEY)
            assert [row.entry_id for row in orders] == ["e1"]
            assert second.pending_ids(STRATEGY_KEY) == []
        finally:
            second.close()


class TestIdRecordModel:
    def test_id_record_holds_parsed_intent(self):
        record = IdRecord(
            id="cid1",
            used=False,
            type="ORDER",
            strategy_key=STRATEGY_KEY,
            intent=make_entry_intent(),
            created_at="2026-10-07T12:00:00+00:00",
        )

        assert record.intent.kind is IntentKind.ENTRY
