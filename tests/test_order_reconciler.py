"""OrderReconciler（启动对账）测试

覆盖: entry 重建 / exit 回填 / 无单核销 / 部分成交恢复 / 太新跳过 / 查询异常容忍。
"""

from collections.abc import Iterator

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.execution.order_reconciler import OrderReconciler
from smart_trader.model import (
    OrderResult,
    OrderSide,
    OrderStatus,
)
from smart_trader.storage.order_journal import OrderJournal, OrderRole
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeTradingPort,
    make_entry_intent,
    make_exit_intent,
    make_order_record,
    make_position_row,
)

OTHER_KEY = "qqqusdt_long_buy_1m"


@pytest.fixture
def journal() -> Iterator[OrderJournal]:
    instance = OrderJournal(":memory:")
    yield instance
    instance.close()


def build_reconciler(
    trading: FakeTradingPort,
    journal: OrderJournal,
    min_age_seconds: float = 0.0,
) -> OrderReconciler:
    return OrderReconciler(
        journal=journal,
        trading=trading,
        strategy_key=DEFAULT_STRATEGY_KEY,
        min_age_seconds=min_age_seconds,
    )


def exchange_order(
    custom_id: str = "BUYexchange1",
    status: OrderStatus = OrderStatus.OPEN,
    filled_qty: float = 0.0,
    price: float | None = 0.5,
) -> OrderResult:
    return OrderResult(
        custom_id=custom_id,
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        status=status,
        quantity=100.0,
        filled_qty=filled_qty,
        price=price,
    )


class TestRecoverEntry:
    def test_recovers_entry_order_from_snapshot(self, journal: OrderJournal) -> None:
        custom_id = journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(exchange_order(filled_qty=30.0))
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.recovered == 1
        orders = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert len(orders) == 1, "对账必须把交易所已存在的订单补回账本"
        assert orders[0].filled_quantity == pytest.approx(30.0), "实际成交量以交易所为准"
        assert orders[0].fixed_take_profit_rate == pytest.approx(0.01), (
            "策略参数来自意图快照"
        )
        records = journal.load_order_records(DEFAULT_STRATEGY_KEY)
        assert len(records) == 1, "恢复的订单必须补录流水"
        assert records[0].role is OrderRole.ENTRY
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []
        assert trading.queries == [custom_id], "必须用预留订单号去查"

    def test_recovers_exchange_rewritten_custom_id(self, journal: OrderJournal) -> None:
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(exchange_order(custom_id="BUYrewritten"))
        reconciler = build_reconciler(trading, journal)

        reconciler.reconcile()

        orders = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert orders[0].entry_id == "BUYrewritten"

    def test_partial_fill_on_canceled_still_recovers(self, journal: OrderJournal) -> None:
        """已撤销但有部分成交: 真实成交必须恢复, 不能当作无单核销"""
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(
            exchange_order(status=OrderStatus.CANCELED, filled_qty=40.0)
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.recovered == 1
        orders = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert orders[0].filled_quantity == pytest.approx(40.0)

    def test_settles_when_order_not_found(self, journal: OrderJournal) -> None:
        custom_id = journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(None)
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.settled == 1
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == [], "查不到即核销"
        assert journal.load_positions(DEFAULT_STRATEGY_KEY) == []
        assert trading.queries == [custom_id]

    def test_settles_when_rejected_without_fill(self, journal: OrderJournal) -> None:
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(exchange_order(status=OrderStatus.REJECTED))
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.settled == 1
        assert journal.load_positions(DEFAULT_STRATEGY_KEY) == []


class TestRecoverExit:
    def test_recovers_exit_into_entry_rows(self, journal: OrderJournal) -> None:
        first = journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        journal.confirm_entry(
            first,
            make_position_row("BUYentry1"),
            make_order_record("in-BUYentry1"),
        )
        journal.reserve(DEFAULT_STRATEGY_KEY, make_exit_intent(["BUYentry1"]))
        trading = FakeTradingPort()
        trading.queue_query(
            OrderResult(
                custom_id="SELLexit1",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                price=0.55,
            )
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.recovered == 1
        orders = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert orders[0].exit_id == "SELLexit1"
        assert orders[0].exit_price == pytest.approx(0.55)
        roles = [
            record.role for record in journal.load_order_records(DEFAULT_STRATEGY_KEY)
        ]
        assert roles.count(OrderRole.EXIT) == 1, "出场单恢复必须补录流水"
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []


class TestRobustness:
    def test_skips_too_fresh_records(self, journal: OrderJournal) -> None:
        """刚预留的订单可能还没在交易所 settle, 先跳过留给下一次"""
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        reconciler = build_reconciler(trading, journal, min_age_seconds=60.0)

        summary = reconciler.reconcile()

        assert summary.skipped == 1
        assert len(journal.pending_ids(DEFAULT_STRATEGY_KEY)) == 1, "不得误核销"
        assert trading.queries == [], "太新的记录不应打扰交易所"

    def test_query_error_keeps_pending(self, journal: OrderJournal) -> None:
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "查询超时")
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.failed == 1
        assert len(journal.pending_ids(DEFAULT_STRATEGY_KEY)) == 1, (
            "查询失败绝不能核销, 否则线索丢失"
        )

    def test_one_failure_does_not_stop_others(self, journal: OrderJournal) -> None:
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "查询超时"),
            None,
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.failed == 1
        assert summary.settled == 1
        remaining = journal.pending_ids(DEFAULT_STRATEGY_KEY)
        assert len(remaining) == 1, "查询失败的那条必须保留待下次对账"

    def test_only_touches_own_strategy(self, journal: OrderJournal) -> None:
        own = journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        journal.reserve(OTHER_KEY, make_entry_intent())
        trading = FakeTradingPort()
        reconciler = build_reconciler(trading, journal)

        reconciler.reconcile()

        assert trading.queries == [own], "不能替其他策略对账"
        assert len(journal.pending_ids(OTHER_KEY)) == 1

    def test_reconcile_is_idempotent(self, journal: OrderJournal) -> None:
        journal.reserve(DEFAULT_STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        reconciler = build_reconciler(trading, journal)

        first = reconciler.reconcile()
        second = reconciler.reconcile()

        assert first.recovered + first.settled == 1
        assert second.recovered == 0, "已核销的记录不应再次处理"
        assert second.settled == 0
        assert len(trading.queries) == 1, "对账完成后不应再查交易所"
