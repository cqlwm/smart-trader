"""OrderJournal（sqlite 订单日志）测试

两张表: grid_positions（逻辑仓位）、orders（订单流水, status 为空 = 结果未确认）。
覆盖: id 生成 / 首单同事务 / 预写追加 / 回写 / 删行复用 / upsert / 归档 /
加载（活跃仓位、关联订单、待对账）/ 事务原子性 / 多策略隔离 / 持久化。
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.model import OrderSide, OrderStatus, PositionSide
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord
from tests.fakes import make_order_record, make_position_row

STRATEGY_KEY = "dogeusdt_long_buy_1m"
OTHER_KEY = "qqqusdt_long_buy_1m"


@pytest.fixture
def journal() -> Iterator[OrderJournal]:
    instance = OrderJournal(":memory:")
    yield instance
    instance.close()


def seed_open(
    journal: OrderJournal,
    position_id: str = "POS1",
    order_id: str = "BUYo1",
    strategy_key: str = STRATEGY_KEY,
    **order_overrides: object,
) -> None:
    """写一个仓位 + 一条首单"""
    journal.open_position(
        make_position_row(position_id, strategy_key=strategy_key),
        make_order_record(
            order_id, position_id, strategy_key=strategy_key, **order_overrides
        ),
    )


class TestGenerateIds:
    def test_position_ids_are_unique_and_non_empty(self, journal: OrderJournal):
        ids = {journal.generate_position_id() for _ in range(20)}
        assert len(ids) == 20, "仓位号必须互不相同"
        assert all(ids), "仓位号不能为空"

    def test_order_id_carries_side_prefix(self, journal: OrderJournal):
        assert journal.generate_order_id(OrderSide.BUY).startswith("buy")
        assert journal.generate_order_id(OrderSide.SELL).startswith("sell")
        assert journal.generate_order_id(OrderSide.BUY) != journal.generate_order_id(
            OrderSide.BUY
        )


class TestOpenPosition:
    def test_writes_position_and_first_order_atomically(self, journal: OrderJournal):
        journal.open_position(
            make_position_row("POS1"), make_order_record("BUYo1", "POS1")
        )

        positions = journal.load_active_positions(STRATEGY_KEY)
        assert [row.id for row in positions] == ["POS1"]
        orders = journal.load_orders_for_positions(["POS1"])
        assert [row.order_id for row in orders] == ["BUYo1"]
        assert orders[0].grid_position_id == "POS1", "订单必须归属该仓位"
        assert orders[0].status is OrderStatus.OPEN

    def test_generates_ids_when_empty(self, journal: OrderJournal):
        journal.open_position(
            make_position_row(""), make_order_record("", "", strategy_key=STRATEGY_KEY)
        )

        positions = journal.load_active_positions(STRATEGY_KEY)
        assert len(positions) == 1 and positions[0].id, "空 id 必须自动生成"
        orders = journal.load_orders_for_positions([positions[0].id])
        assert len(orders) == 1 and orders[0].order_id, "空订单号必须自动生成"

    def test_returns_position_and_order_ids(self, journal: OrderJournal):
        position_id, order_id = journal.open_position(
            make_position_row("POS9"), make_order_record("BUYo9", "POS9")
        )
        assert (position_id, order_id) == ("POS9", "BUYo9")

    def test_preserves_all_position_fields(self, journal: OrderJournal):
        row = make_position_row(
            "POS1",
            opening_quantity=5.0,
            opening_price=0.4,
            enable_stop_loss=True,
            stop_loss_rate=0.05,
            enable_trailing_stop=True,
            trailing_stop_rate=0.02,
            trailing_stop_activation_rate=0.01,
            trailing_stop_price=0.47,
        )
        journal.open_position(row, make_order_record("BUYo1", "POS1"))

        loaded = journal.load_active_positions(STRATEGY_KEY)[0]
        assert loaded.model_dump(exclude={"created_at", "updated_at"}) == row.model_dump(
            exclude={"created_at", "updated_at"}
        ), "往返后除时间戳外字段必须完全一致"

    def test_existing_order_id_conflict_is_detected(self, journal: OrderJournal):
        """首单订单号冲突（PK 已存在）必须被感知, 而非静默覆盖"""
        journal.append_order(make_order_record("BUYo1", "POS1"))

        with pytest.raises(RuntimeError):
            journal.open_position(
                make_position_row("POS1", strategy_key=STRATEGY_KEY),
                make_order_record("BUYo1", "POS1"),
            )


class TestAppendOrder:
    def test_appends_order_row_without_status(self, journal: OrderJournal):
        order_id = journal.append_order(
            make_order_record("BUYo1", "POS1", status=None)
        )

        assert order_id == "BUYo1"
        orders = journal.load_orders_for_positions(["POS1"])
        assert len(orders) == 1
        assert orders[0].status is None, "预写行的 status 必须为空"
        assert orders[0].quantity == pytest.approx(100.0)

    def test_generates_id_when_empty(self, journal: OrderJournal):
        order_id = journal.append_order(
            make_order_record("", "POS1", strategy_key=STRATEGY_KEY)
        )
        assert order_id
        assert journal.load_orders_for_positions(["POS1"])[0].order_id == order_id


class TestSettleOrder:
    def test_writes_back_exchange_facts(self, journal: OrderJournal):
        journal.append_order(make_order_record("BUYo1", "POS1", status=None))

        journal.settle_order(
            make_order_record(
                "BUYo1",
                "POS1",
                status=OrderStatus.CLOSED,
                filled_qty=40.0,
                avg_price=0.52,
            )
        )

        order = journal.load_orders_for_positions(["POS1"])[0]
        assert order.status is OrderStatus.CLOSED
        assert order.filled_qty == pytest.approx(40.0)
        assert order.avg_price == pytest.approx(0.52)

    def test_unknown_order_is_noop(self, journal: OrderJournal):
        journal.settle_order(make_order_record("ghost", "POS1"))
        assert journal.load_orders_for_positions(["POS1"]) == []


class TestDiscardOrder:
    def test_deletes_discarded_row(self, journal: OrderJournal):
        journal.append_order(make_order_record("BUYo1", "POS1", status=None))

        journal.discard_order("BUYo1")

        assert journal.load_orders_for_positions(["POS1"]) == []
        assert journal.pending_orders(STRATEGY_KEY) == []

    def test_discarded_id_can_be_reused(self, journal: OrderJournal):
        journal.append_order(make_order_record("BUYo1", "POS1", status=None))
        journal.discard_order("BUYo1")

        reused = journal.append_order(
            make_order_record("BUYo1", "POS1", status=OrderStatus.OPEN)
        )

        assert reused == "BUYo1", "确认从未存在的订单号必须可复用"
        assert len(journal.load_orders_for_positions(["POS1"])) == 1


class TestUpsertPosition:
    def test_inserts_and_updates_row(self, journal: OrderJournal):
        journal.upsert_position(make_position_row("POS1", trailing_stop_price=0.4))
        journal.upsert_position(make_position_row("POS1", trailing_stop_price=0.45))

        positions = journal.load_active_positions(STRATEGY_KEY)
        assert len(positions) == 1
        assert positions[0].trailing_stop_price == pytest.approx(0.45)

    def test_preserves_first_write_created_at(self, journal: OrderJournal):
        journal.upsert_position(
            make_position_row("POS1", created_at="2026-01-01T00:00:00+00:00")
        )
        journal.upsert_position(
            make_position_row(
                "POS1",
                created_at="2030-12-31T00:00:00+00:00",
                trailing_stop_price=0.5,
            )
        )

        row = journal.load_active_positions(STRATEGY_KEY)[0]
        assert row.created_at == "2026-01-01T00:00:00+00:00", (
            "created_at 是首写事实, 二次 upsert 不得覆盖"
        )
        assert row.trailing_stop_price == pytest.approx(0.5)


class TestClosePosition:
    def test_marks_position_archived(self, journal: OrderJournal):
        seed_open(journal, "POS1")

        journal.close_position("POS1")

        assert journal.load_active_positions(STRATEGY_KEY) == []
        assert journal.load_orders_for_positions(["POS1"]) != [], "归档不删订单行"


class TestLoadActivePositions:
    def test_only_returns_active(self, journal: OrderJournal):
        seed_open(journal, "POS1")
        seed_open(journal, "POS2", order_id="BUYo2")

        journal.close_position("POS2")

        assert [row.id for row in journal.load_active_positions(STRATEGY_KEY)] == ["POS1"]

    def test_isolated_by_strategy_key(self, journal: OrderJournal):
        seed_open(journal, "POS1", strategy_key=STRATEGY_KEY)
        seed_open(journal, "POS9", order_id="o9", strategy_key=OTHER_KEY)

        assert [row.id for row in journal.load_active_positions(STRATEGY_KEY)] == ["POS1"]
        assert [row.id for row in journal.load_active_positions(OTHER_KEY)] == ["POS9"]

    def test_ordered_by_insertion(self, journal: OrderJournal):
        for index in range(3):
            journal.upsert_position(make_position_row(f"POS{index}"))
        assert [row.id for row in journal.load_active_positions(STRATEGY_KEY)] == [
            "POS0",
            "POS1",
            "POS2",
        ]


class TestLoadOrdersForPositions:
    def test_empty_ids_returns_empty(self, journal: OrderJournal):
        seed_open(journal, "POS1")
        assert journal.load_orders_for_positions([]) == []

    def test_returns_only_given_positions(self, journal: OrderJournal):
        seed_open(journal, "POS1", order_id="o1")
        seed_open(journal, "POS2", order_id="o2")

        orders = journal.load_orders_for_positions(["POS1"])
        assert [order.order_id for order in orders] == ["o1"]

    def test_isolated_by_position(self, journal: OrderJournal):
        seed_open(journal, "POS1", order_id="o1")
        journal.append_order(make_order_record("o2", "POS2", status=OrderStatus.OPEN))

        assert [o.order_id for o in journal.load_orders_for_positions(["POS2"])] == ["o2"]


class TestPendingOrders:
    def test_only_returns_unconfirmed(self, journal: OrderJournal):
        journal.append_order(make_order_record("pending", "POS1", status=None))
        journal.append_order(
            make_order_record("settled", "POS1", status=OrderStatus.OPEN)
        )

        pending = journal.pending_orders(STRATEGY_KEY)
        assert [order.order_id for order in pending] == ["pending"]

    def test_isolated_by_strategy_key(self, journal: OrderJournal):
        journal.append_order(make_order_record("p1", "POS1", status=None))
        journal.append_order(
            make_order_record("p2", "POS2", strategy_key=OTHER_KEY, status=None)
        )

        assert [o.order_id for o in journal.pending_orders(STRATEGY_KEY)] == ["p1"]
        assert [o.order_id for o in journal.pending_orders(OTHER_KEY)] == ["p2"]


class TestPersistence:
    def test_survives_reopen(self, tmp_path: Path):
        path = str(tmp_path / "orders.db")
        first = OrderJournal(path)
        seed_open(first, "POS1", order_id="BUYo1")
        first.append_order(make_order_record("BUYo2", "POS1", status=None))
        first.close()

        second = OrderJournal(path)
        try:
            assert [row.id for row in second.load_active_positions(STRATEGY_KEY)] == [
                "POS1"
            ]
            assert len(second.load_orders_for_positions(["POS1"])) == 2
            assert [o.order_id for o in second.pending_orders(STRATEGY_KEY)] == ["BUYo2"]
        finally:
            second.close()


class TestModels:
    def test_position_row_forbids_extra_fields(self):
        with pytest.raises(ValueError):
            GridPositionRow.model_validate(
                {**make_position_row().model_dump(), "unknown": 1}
            )

    def test_order_record_forbids_extra_fields(self):
        with pytest.raises(ValueError):
            OrderRecord.model_validate(
                {**make_order_record().model_dump(), "unknown": 1}
            )

    def test_order_record_status_is_optional(self):
        record = make_order_record("o1", "POS1", status=None)
        assert record.status is None, "status 为空表示结果未确认"

    def test_position_row_defaults(self):
        row = make_position_row("POS1")
        assert row.position_side is PositionSide.LONG
        assert row.main_order_side is OrderSide.BUY
        assert row.active is True
        assert row.trailing_stop_price is None