"""JournaledTradingPort（记账下单包装）测试

覆盖: 预留/确认/核销的完整分支, 结果未知保留待对账, 透传行为。
"""

from collections.abc import Iterator

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.execution.journaled_trading import JournaledTradingPort
from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderIntent, OrderJournal, OrderRole
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeTradingPort,
    make_entry_intent,
    make_exit_intent,
)


@pytest.fixture
def journal() -> Iterator[OrderJournal]:
    instance = OrderJournal(":memory:")
    yield instance
    instance.close()


def build_request(custom_id: str = "BUYlocal1") -> OrderRequest:
    return OrderRequest(
        custom_id=custom_id,
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=100.0,
        price=0.5,
        position_side=PositionSide.LONG,
    )


def build_placer(
    trading: FakeTradingPort,
    journal: OrderJournal,
    intent: OrderIntent | None = None,
) -> JournaledTradingPort:
    return JournaledTradingPort(
        trading=trading,
        journal=journal,
        strategy_key=DEFAULT_STRATEGY_KEY,
        intent=intent or make_entry_intent(),
    )


def open_result(custom_id: str = "BUYexchange1") -> OrderResult:
    return OrderResult(
        custom_id=custom_id,
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        status=OrderStatus.OPEN,
        quantity=100.0,
        filled_qty=0.0,
        price=0.5,
    )


class TestPlaceSuccess:
    def test_success_writes_position_and_record_and_settles_id(
        self, journal: OrderJournal
    ) -> None:
        trading = FakeTradingPort()
        trading.queue_place(open_result())
        placer = build_placer(trading, journal)

        result = placer.place(build_request())

        assert result.status is OrderStatus.OPEN
        positions = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1, "挂上成功的仓位必须落库"
        assert positions[0].entry_id == "BUYexchange1", "以交易所返回的订单号为准"
        assert positions[0].fixed_take_profit_rate == pytest.approx(0.01), (
            "策略参数来自意图快照"
        )
        records = journal.load_order_records(DEFAULT_STRATEGY_KEY)
        assert len(records) == 1, "订单流水必须同步记录"
        assert records[0].order_id == "BUYexchange1"
        assert records[0].role is OrderRole.ENTRY
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == [], "成功即核销"

    def test_preferred_id_is_registered_before_place(self, journal: OrderJournal) -> None:
        """下发给交易所的订单号必须先在 id 表登记 (用结果未知场景验证)"""
        trading = FakeTradingPort()
        trading.queue_place(SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "超时"))
        placer = build_placer(trading, journal)

        with pytest.raises(SmartTraderError):
            placer.place(build_request("BUYpreferred1"))

        assert trading.placed[0].custom_id == "BUYpreferred1", (
            "无冲突时沿用调用方期望的订单号"
        )
        pending = journal.pending_ids(DEFAULT_STRATEGY_KEY)
        assert [record.id for record in pending] == ["BUYpreferred1"], (
            "登记发生在上单之前: 未收到明确结果就保留在待对账清单"
        )

    def test_closed_result_is_recorded_with_filled_quantity(
        self, journal: OrderJournal
    ) -> None:
        trading = FakeTradingPort()
        trading.queue_place(
            OrderResult(
                custom_id="BUYexchange1",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
                price=0.5,
            )
        )
        placer = build_placer(trading, journal)

        placer.place(build_request())

        positions = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert positions[0].status is OrderStatus.CLOSED
        assert positions[0].filled_quantity == pytest.approx(100.0)

    def test_exit_success_backfills_entry_rows(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        placer = build_placer(trading, journal)
        trading.queue_place(open_result("BUYentry1"))
        placer.place(build_request())

        exit_placer = build_placer(
            trading, journal, intent=make_exit_intent(["BUYentry1"])
        )
        trading.queue_place(
            OrderResult(
                custom_id="SELLexit1",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                price=0.55,
            )
        )
        exit_placer.place(build_request("SELLpreferred"))

        positions = journal.load_positions(DEFAULT_STRATEGY_KEY)
        assert positions[0].exit_id == "SELLexit1"
        assert positions[0].exit_price == pytest.approx(0.55)
        roles = [
            record.role for record in journal.load_order_records(DEFAULT_STRATEGY_KEY)
        ]
        assert roles == [OrderRole.ENTRY, OrderRole.EXIT], "入场与出场的流水都要记录"
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []


class TestPlaceRejection:
    @pytest.mark.parametrize(
        "error_code",
        [
            ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY,
            ErrorCode.ORDER_REJECTED,
            ErrorCode.INSUFFICIENT_FUNDS,
            ErrorCode.ORDER_NOTIONAL_TOO_SMALL,
            ErrorCode.EXCHANGE_RATE_LIMITED,
        ],
    )
    def test_deterministic_rejection_settles_without_order(
        self, journal: OrderJournal, error_code: ErrorCode
    ) -> None:
        """确定未成交: 核销订单号, 不留下幽灵待对账记录"""
        trading = FakeTradingPort()
        trading.queue_place(SmartTraderError(error_code, "被拒绝"))
        placer = build_placer(trading, journal)

        with pytest.raises(SmartTraderError):
            placer.place(build_request())

        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_positions(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_order_records(DEFAULT_STRATEGY_KEY) == [], "被拒不入流水"

    def test_rejected_result_without_exception_settles(self, journal: OrderJournal):
        """交易所未抛异常但直接回 rejected: 同样核销"""
        trading = FakeTradingPort()
        trading.queue_place(
            OrderResult(
                custom_id="BUYexchange1",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.REJECTED,
                quantity=100.0,
            )
        )
        placer = build_placer(trading, journal)

        result = placer.place(build_request())

        assert result.status is OrderStatus.REJECTED
        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_positions(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_order_records(DEFAULT_STRATEGY_KEY) == [], "被拒不入流水"


class TestPlaceIndeterminate:
    @pytest.mark.parametrize(
        "error_code",
        [
            ErrorCode.EXCHANGE_UNAVAILABLE,
            ErrorCode.ORDER_RESPONSE_INVALID,
            ErrorCode.EXCHANGE_ERROR,
        ],
    )
    def test_indeterminate_error_keeps_pending_for_reconcile(
        self, journal: OrderJournal, error_code: ErrorCode
    ) -> None:
        """结果未知: 必须保持待对账, 留给对账器去交易所确认"""
        trading = FakeTradingPort()
        trading.queue_place(SmartTraderError(error_code, "网络中断"))
        placer = build_placer(trading, journal)

        with pytest.raises(SmartTraderError):
            placer.place(build_request())

        pending = journal.pending_ids(DEFAULT_STRATEGY_KEY)
        assert len(pending) == 1, "结果未知的订单号必须保留, 否则幽灵订单永远找不回"
        assert pending[0].id == "BUYlocal1"

    def test_unexpected_exception_keeps_pending(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        trading.queue_place(RuntimeError("boom"))
        placer = build_placer(trading, journal)

        with pytest.raises(RuntimeError):
            placer.place(build_request())

        assert len(journal.pending_ids(DEFAULT_STRATEGY_KEY)) == 1


class TestPassThrough:
    def test_query_and_cancel_are_forwarded(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        placer = build_placer(trading, journal)

        placer.query("cid1", DEFAULT_SYMBOL)
        placer.cancel("cid1", DEFAULT_SYMBOL)

        assert trading.queries == ["cid1"]
        assert trading.canceled == ["cid1"]

    def test_query_does_not_touch_journal(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        placer = build_placer(trading, journal)

        placer.query("cid1", DEFAULT_SYMBOL)

        assert journal.pending_ids(DEFAULT_STRATEGY_KEY) == []
