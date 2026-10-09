"""JournaledTradingPort（记账下单包装）测试

覆盖: 首单同事务建仓位 / 预写追加 / 结果回写 / 结果未知保留待对账 /
确定未成交删行且 id 可复用 / 成功即作废 id / 透传行为。
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
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeTradingPort,
    make_position_row,
)


@pytest.fixture
def journal() -> Iterator[OrderJournal]:
    instance = OrderJournal(":memory:")
    yield instance
    instance.close()


def build_request() -> OrderRequest:
    return OrderRequest(
        custom_id="",
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
    position: GridPositionRow | None = None,
    grid_position_id: str | None = None,
) -> JournaledTradingPort:
    return JournaledTradingPort(
        trading=trading,
        journal=journal,
        strategy_key=DEFAULT_STRATEGY_KEY,
        position=position,
        grid_position_id=grid_position_id,
    )


def open_result(filled_qty: float = 0.0) -> OrderResult:
    return OrderResult(
        custom_id="echo",
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        status=OrderStatus.OPEN,
        quantity=100.0,
        filled_qty=filled_qty,
        price=0.5,
    )


class TestConstruction:
    def test_requires_exactly_one_target(self, journal: OrderJournal):
        trading = FakeTradingPort()
        with pytest.raises(ValueError):
            build_placer(trading, journal)
        with pytest.raises(ValueError):
            build_placer(
                trading, journal, position=make_position_row(), grid_position_id="POS1"
            )


class TestPlaceNewPosition:
    def test_opens_position_and_first_order_together(self, journal: OrderJournal):
        trading = FakeTradingPort()
        trading.queue_place(open_result())
        placer = build_placer(trading, journal, position=make_position_row("POS1"))

        result = placer.place(build_request())

        assert result.status is OrderStatus.OPEN
        positions = journal.load_active_positions(DEFAULT_STRATEGY_KEY)
        assert [row.id for row in positions] == ["POS1"], "首单必须同事务建仓位"
        orders = journal.load_orders_for_positions(["POS1"])
        assert len(orders) == 1, "订单行必须同步写入"
        assert orders[0].grid_position_id == "POS1"
        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == [], "成功即回写"

    def test_generates_position_id_when_empty(self, journal: OrderJournal):
        trading = FakeTradingPort()
        trading.queue_place(open_result())
        placer = build_placer(trading, journal, position=make_position_row(""))

        placer.place(build_request())

        positions = journal.load_active_positions(DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1 and positions[0].id

    def test_success_settles_fill_and_invalidates_id(self, journal: OrderJournal):
        trading = FakeTradingPort()
        trading.queue_place(
            OrderResult(
                custom_id="echo",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
                avg_price=0.5,
            )
        )
        placer = build_placer(trading, journal, position=make_position_row("POS1"))

        placer.place(build_request())
        order = journal.load_orders_for_positions(["POS1"])[0]
        assert order.status is OrderStatus.CLOSED
        assert order.filled_qty == pytest.approx(100.0)
        assert order.avg_price == pytest.approx(0.5)


class TestPlaceAppend:
    def test_appends_order_to_existing_position(self, journal: OrderJournal):
        journal.upsert_position(make_position_row("POS1"))
        trading = FakeTradingPort()
        trading.queue_place(open_result(filled_qty=40.0))
        placer = build_placer(trading, journal, grid_position_id="POS1")

        placer.place(build_request())

        orders = journal.load_orders_for_positions(["POS1"])
        assert len(orders) == 1
        assert orders[0].grid_position_id == "POS1"
        assert orders[0].filled_qty == pytest.approx(40.0)

    def test_success_uses_a_fresh_id_for_next_order(self, journal: OrderJournal):
        """订单确曾存在后 id 作废: 同一次动作之外必须换新 id"""
        journal.upsert_position(make_position_row("POS1"))
        trading = FakeTradingPort()
        trading.queue_place(open_result(), open_result())

        first = build_placer(trading, journal, grid_position_id="POS1")
        first.place(build_request())
        second = build_placer(trading, journal, grid_position_id="POS1")
        second.place(build_request())

        assert trading.placed[0].custom_id != trading.placed[1].custom_id
        assert len(journal.load_orders_for_positions(["POS1"])) == 2


class TestPlaceIndeterminate:
    @pytest.mark.parametrize(
        "error_code",
        [
            ErrorCode.EXCHANGE_UNAVAILABLE,
            ErrorCode.ORDER_RESPONSE_INVALID,
            ErrorCode.EXCHANGE_ERROR,
        ],
    )
    def test_keeps_prewritten_row_for_reconcile(
        self, journal: OrderJournal, error_code: ErrorCode
    ) -> None:
        trading = FakeTradingPort()
        trading.queue_place(SmartTraderError(error_code, "网络中断"))
        placer = build_placer(trading, journal, position=make_position_row("POS1"))

        with pytest.raises(SmartTraderError):
            placer.place(build_request())

        pending = journal.pending_orders(DEFAULT_STRATEGY_KEY)
        assert len(pending) == 1, "结果未知必须保留 status 为空, 留给对账"
        assert pending[0].status is None
        assert pending[0].grid_position_id == "POS1"

    def test_unexpected_exception_keeps_row(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        trading.queue_place(RuntimeError("boom"))
        placer = build_placer(trading, journal, grid_position_id="POS1")

        with pytest.raises(RuntimeError):
            placer.place(build_request())

        assert len(journal.pending_orders(DEFAULT_STRATEGY_KEY)) == 1


class TestPlaceSettled:
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
    def test_deterministic_rejection_discards_row(
        self, journal: OrderJournal, error_code: ErrorCode
    ) -> None:
        trading = FakeTradingPort()
        trading.queue_place(SmartTraderError(error_code, "被拒绝"))
        placer = build_placer(trading, journal, grid_position_id="POS1")

        with pytest.raises(SmartTraderError):
            placer.place(build_request())

        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == []
        assert journal.load_orders_for_positions(["POS1"]) == [], "从未存在不留下行"

    def test_settled_error_reuses_order_id(self, journal: OrderJournal) -> None:
        """GTX 被拒后重试必须复用同一 order_id（不堆行）"""
        journal.upsert_position(make_position_row("POS1"))
        trading = FakeTradingPort()
        trading.queue_place(
            SmartTraderError(ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY, "会立即成交"),
            open_result(),
        )
        placer = build_placer(trading, journal, grid_position_id="POS1")

        with pytest.raises(SmartTraderError):
            placer.place(build_request())
        placer.place(build_request())

        assert trading.placed[0].custom_id == trading.placed[1].custom_id, (
            "确认从未存在的订单号必须复用"
        )
        assert len(journal.load_orders_for_positions(["POS1"])) == 1

    def test_rejected_result_without_exception_is_recorded(
        self, journal: OrderJournal
    ):
        """交易所直接回 rejected: 订单确曾到达交易所, 回写后作废 id"""
        journal.upsert_position(make_position_row("POS1"))
        trading = FakeTradingPort()
        trading.queue_place(
            OrderResult(
                custom_id="echo",
                symbol=DEFAULT_SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.REJECTED,
                quantity=100.0,
            )
        )
        placer = build_placer(trading, journal, grid_position_id="POS1")

        result = placer.place(build_request())

        assert result.status is OrderStatus.REJECTED
        orders = journal.load_orders_for_positions(["POS1"])
        assert len(orders) == 1 and orders[0].status is OrderStatus.REJECTED
        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == []


class TestPassThrough:
    def test_query_and_cancel_are_forwarded(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        placer = build_placer(trading, journal, grid_position_id="POS1")

        placer.query("cid1", DEFAULT_SYMBOL)
        placer.cancel("cid1", DEFAULT_SYMBOL)

        assert trading.queries == ["cid1"]
        assert trading.canceled == ["cid1"]

    def test_query_does_not_touch_journal(self, journal: OrderJournal) -> None:
        trading = FakeTradingPort()
        placer = build_placer(trading, journal, grid_position_id="POS1")

        placer.query("cid1", DEFAULT_SYMBOL)

        assert journal.pending_orders(DEFAULT_STRATEGY_KEY) == []