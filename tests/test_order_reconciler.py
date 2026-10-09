"""OrderReconciler（启动对账）测试

覆盖: 交易所已存在的订单回写 / 部分成交恢复 / 无单删行 / 太新跳过 /
查询异常容忍 / 多策略隔离 / 幂等。
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
from smart_trader.storage.order_journal import OrderJournal
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeTradingPort,
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
    custom_id: str = "BUYo1",
    status: OrderStatus = OrderStatus.OPEN,
    filled_qty: float = 0.0,
    price: float | None = 0.5,
    quantity: float = 100.0,
) -> OrderResult:
    return OrderResult(
        custom_id=custom_id,
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        status=status,
        quantity=quantity,
        filled_qty=filled_qty,
        price=price,
        avg_price=price,
    )


def seed_pending(
    journal: OrderJournal,
    order_id: str = "BUYo1",
    strategy_key: str = DEFAULT_STRATEGY_KEY,
) -> None:
    journal.upsert_position(make_position_row("POS1", strategy_key=strategy_key))
    journal.append_order(
        make_order_record(
            order_id, "POS1", strategy_key=strategy_key, status=None
        )
    )


class TestRecover:
    def test_recovers_live_order_from_exchange(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(exchange_order(filled_qty=30.0))
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.recovered == 1
        orders = journal.load_orders_for_positions(["POS1"])
        assert len(orders) == 1
        assert orders[0].status is OrderStatus.OPEN
        assert orders[0].filled_qty == pytest.approx(30.0), "实际成交量以交易所为准"
        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == []
        assert trading.queries == ["BUYo1"], "必须用预写订单号去查"

    def test_partial_fill_on_canceled_still_recovers(self, journal: OrderJournal) -> None:
        """已撤销但有部分成交: 真实成交必须恢复, 不能删行丢弃"""
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(
            exchange_order(status=OrderStatus.CANCELED, filled_qty=40.0)
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.recovered == 1
        assert journal.load_orders_for_positions(["POS1"])[0].filled_qty == pytest.approx(
            40.0
        )

    def test_closed_order_recovers(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(
            exchange_order(status=OrderStatus.CLOSED, filled_qty=100.0)
        )
        reconciler = build_reconciler(trading, journal)

        reconciler.reconcile()

        order = journal.load_orders_for_positions(["POS1"])[0]
        assert order.status is OrderStatus.CLOSED
        assert order.filled_qty == pytest.approx(100.0)


class TestDiscard:
    def test_discards_when_order_not_found(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(None)
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.settled == 1
        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_orders_for_positions(["POS1"]) == [], "查不到即删行"
        assert trading.queries == ["BUYo1"]

    def test_discards_rejected_without_fill(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(exchange_order(status=OrderStatus.REJECTED))
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.settled == 1
        assert journal.load_orders_for_positions(["POS1"]) == []


class TestRobustness:
    def test_skips_too_fresh_records(self, journal: OrderJournal) -> None:
        """刚预写的订单可能还没在交易所 settle, 先跳过留给下一次"""
        seed_pending(journal)
        trading = FakeTradingPort()
        reconciler = build_reconciler(trading, journal, min_age_seconds=60.0)

        summary = reconciler.reconcile()

        assert summary.skipped == 1
        assert len(journal.pending_orders(DEFAULT_STRATEGY_KEY)) == 1, "不得误删"
        assert trading.queries == [], "太新的记录不应打扰交易所"

    def test_query_error_keeps_pending(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "查询超时")
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.failed == 1
        assert len(journal.pending_orders(DEFAULT_STRATEGY_KEY)) == 1, (
            "查询失败绝不能删行, 否则线索丢失"
        )

    def test_one_failure_does_not_stop_others(self, journal: OrderJournal) -> None:
        seed_pending(journal, "BUYo1")
        seed_pending(journal, "BUYo2")
        trading = FakeTradingPort()
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "查询超时"),
            None,
        )
        reconciler = build_reconciler(trading, journal)

        summary = reconciler.reconcile()

        assert summary.failed == 1
        assert summary.settled == 1
        remaining = journal.pending_orders(DEFAULT_STRATEGY_KEY)
        assert len(remaining) == 1, "查询失败的那条必须保留待下次对账"

    def test_only_touches_own_strategy(self, journal: OrderJournal) -> None:
        seed_pending(journal, "BUYo1", strategy_key=DEFAULT_STRATEGY_KEY)
        seed_pending(journal, "BUYo9", strategy_key=OTHER_KEY)
        trading = FakeTradingPort()
        trading.queue_query(None)
        reconciler = build_reconciler(trading, journal)

        reconciler.reconcile()

        assert trading.queries == ["BUYo1"], "不能替其他策略对账"
        assert len(journal.pending_orders(OTHER_KEY)) == 1

    def test_reconcile_is_idempotent(self, journal: OrderJournal) -> None:
        seed_pending(journal)
        trading = FakeTradingPort()
        trading.queue_query(exchange_order())
        reconciler = build_reconciler(trading, journal)

        first = reconciler.reconcile()
        second = reconciler.reconcile()

        assert first.recovered + first.settled == 1
        assert second.recovered == 0, "已回写的记录不应再次处理"
        assert second.settled == 0
        assert len(trading.queries) == 1, "对账完成后不应再查交易所"