import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.execution.limit_order_chaser import LimitOrderChaser
from smart_trader.model import (
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
    TimeInForce,
)
from tests.fakes import (
    DEFAULT_SYMBOL_INFO,
    FakeMarketDataPort,
    FakeTradingPort,
)

SYMBOL = Symbol(base="DOGE", quote="USDT")
TICK_SIZE = DEFAULT_SYMBOL_INFO.tick_size


def build_chaser(
    trading: FakeTradingPort,
    market: FakeMarketDataPort,
    behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN,
    side: OrderSide = OrderSide.BUY,
    max_retry: int = 3,
    wait_timeout: float = 0.0,
) -> LimitOrderChaser:
    return LimitOrderChaser(
        trading=trading,
        market=market,
        symbol=SYMBOL,
        side=side,
        quantity=100.0,
        position_side=PositionSide.LONG,
        behavior=behavior,
        max_retry=max_retry,
        poll_interval=0.0,
        wait_timeout=wait_timeout,
    )


def open_order(custom_id: str = "cid1", filled_qty: float = 0.0) -> OrderResult:
    return OrderResult(
        custom_id=custom_id,
        symbol=SYMBOL,
        side=OrderSide.BUY,
        status=OrderStatus.OPEN,
        quantity=100.0,
        filled_qty=filled_qty,
    )


def filled_order(custom_id: str = "cid1", filled_qty: float = 100.0) -> OrderResult:
    return OrderResult(
        custom_id=custom_id,
        symbol=SYMBOL,
        side=OrderSide.BUY,
        status=OrderStatus.CLOSED,
        quantity=100.0,
        filled_qty=filled_qty,
    )


def rejected_gtx() -> SmartTraderError:
    return SmartTraderError(ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY, "挂单会被立即成交")


def placed_prices(trading: FakeTradingPort) -> list[float | None]:
    return [request.price for request in trading.placed]


class TestChaseOpenOnly:
    @pytest.mark.parametrize(
        ("side", "expected_prices"),
        [
            (OrderSide.BUY, [0.5, 0.5 - TICK_SIZE, 0.5 - 2 * TICK_SIZE]),
            (OrderSide.SELL, [0.5, 0.5 + TICK_SIZE, 0.5 + 2 * TICK_SIZE]),
        ],
    )
    def test_steps_price_toward_maker(
        self, side: OrderSide, expected_prices: list[float]
    ):
        """挂单失败时向更容易成为 maker 的方向递进: 买单降价, 卖单涨价"""
        trading = FakeTradingPort()
        trading.queue_place(rejected_gtx(), rejected_gtx(), open_order())
        chaser = build_chaser(trading, FakeMarketDataPort(), side=side)

        result = chaser.chase_open_only(0.5)

        assert result is not None
        assert placed_prices(trading) == pytest.approx(expected_prices)

    def test_uses_gtx_time_in_force(self):
        trading = FakeTradingPort()
        trading.queue_place(open_order())
        chaser = build_chaser(trading, FakeMarketDataPort())

        chaser.chase_open_only(0.5)

        request = trading.placed[0]
        assert request.time_in_force is TimeInForce.GTX, "追单必须只做 maker"
        assert request.type is OrderType.LIMIT

    def test_returns_none_after_max_retry(self):
        trading = FakeTradingPort()
        trading.queue_place(*[rejected_gtx() for _ in range(3)])
        chaser = build_chaser(trading, FakeMarketDataPort(), max_retry=3)

        assert chaser.chase_open_only(0.5) is None
        assert len(trading.placed) == 3, "应尝试 max_retry 次"

    def test_keeps_going_on_transient_error(self):
        """网络/限频属于瞬时错误, 换价重试即可"""
        trading = FakeTradingPort()
        trading.queue_place(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "网络超时"), open_order()
        )
        chaser = build_chaser(trading, FakeMarketDataPort())

        assert chaser.chase_open_only(0.5) is not None
        assert placed_prices(trading) == pytest.approx([0.5, 0.5 - TICK_SIZE])

    def test_stops_on_deterministic_rejection(self):
        """确定性拒绝(名义价值/精度)时立刻失败, 不递进价格重试"""
        trading = FakeTradingPort()
        trading.queue_place(
            SmartTraderError(ErrorCode.ORDER_NOTIONAL_TOO_SMALL, "名义价值不足")
        )
        chaser = build_chaser(trading, FakeMarketDataPort())

        with pytest.raises(SmartTraderError) as exc:
            chaser.chase_open_only(0.5)

        assert exc.value.error_code is ErrorCode.ORDER_NOTIONAL_TOO_SMALL
        assert len(trading.placed) == 1, "确定性拒绝不应重试"

    def test_uses_returned_custom_id_when_exchange_rewrites_it(self):
        trading = FakeTradingPort()
        trading.queue_place(open_order(custom_id="exchanged_cid"))
        chaser = build_chaser(trading, FakeMarketDataPort())

        result = chaser.chase_open_only(0.5)

        assert result is not None
        assert result.custom_id == "exchanged_cid"

    def test_rejected_order_result_is_not_treated_as_placed(self):
        """交易所直接回 rejected 状态时没有挂上单, 必须继续递进而不是当作成功"""
        trading = FakeTradingPort()
        trading.queue_place(
            OrderResult(
                custom_id="cid1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.REJECTED,
                quantity=100.0,
            ),
            open_order(),
        )
        chaser = build_chaser(trading, FakeMarketDataPort())

        result = chaser.chase_open_only(0.5)

        assert result is not None
        assert result.status is OrderStatus.OPEN
        assert len(trading.placed) == 2, "被拒后必须递进价格重试"


class TestChaseClosed:
    def test_returns_filled_result_when_filled_while_waiting(self):
        """等待期间成交则直接返回结果, 不撤单"""
        trading = FakeTradingPort()
        trading.queue_query(open_order(), open_order(), filled_order())
        chaser = build_chaser(
            trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER, wait_timeout=5.0
        )

        result = chaser.wait_for_fill("cid1")

        assert result is not None and result.is_filled
        assert trading.canceled == [], "成交后不应撤单"

    def test_returns_none_when_order_canceled(self):
        trading = FakeTradingPort()
        trading.queue_query(
            OrderResult(
                custom_id="cid1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CANCELED,
                quantity=100.0,
            )
        )
        chaser = build_chaser(
            trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER, wait_timeout=5.0
        )

        assert chaser.wait_for_fill("cid1") is None

    def test_cancels_and_rechecks_on_timeout(self):
        """超时未成交: 撤单后再校验一次, 撤单前刚好成交仍算成功"""
        trading = FakeTradingPort()
        trading.queue_query(filled_order())
        chaser = build_chaser(trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER)

        result = chaser.wait_for_fill("cid1")

        assert result is not None and result.is_filled
        assert trading.canceled == ["cid1"]

    def test_returns_none_when_still_unfilled_after_cancel(self):
        trading = FakeTradingPort()
        trading.queue_query(open_order())
        chaser = build_chaser(trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER)

        assert chaser.wait_for_fill("cid1") is None
        assert trading.canceled == ["cid1"]

    def test_returns_partial_fill_instead_of_boolean(self):
        """部分成交必须把真实成交量交回调用方"""
        trading = FakeTradingPort()
        trading.queue_query(filled_order(filled_qty=40.0))
        chaser = build_chaser(trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER)

        result = chaser.wait_for_fill("cid1")

        assert result is not None
        assert result.filled_qty == 40.0, "调用方需要知道实际成交数量"

    def test_keeps_waiting_while_only_partially_filled(self):
        """部分成交但订单仍挂着: 应继续等待, 不能提前返回留下未撤的剩余挂单"""
        trading = FakeTradingPort(status=OrderStatus.OPEN, filled_qty=40.0)
        chaser = build_chaser(
            trading,
            FakeMarketDataPort(),
            PlaceOrderBehavior.CHASER,
            wait_timeout=0.05,
        )
        chaser.poll_interval = 0.01

        result = chaser.wait_for_fill("cid1")

        assert result is not None
        assert result.filled_qty == 40.0, "部分成交量必须回传"
        assert trading.canceled == ["cid1"], "残留挂单必须撤销, 否则留下敞口"

    def test_query_error_does_not_crash_waiting(self):
        trading = FakeTradingPort()
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "网络超时"), filled_order()
        )
        chaser = build_chaser(
            trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER, wait_timeout=5.0
        )

        result = chaser.wait_for_fill("cid1")

        assert result is not None and result.is_filled, "瞬时查询失败不应中断等待"


class TestRun:
    def test_chaser_open_returns_result_without_waiting(self):
        """chaser_open 只下单, 不等待成交"""
        trading = FakeTradingPort()
        trading.queue_place(open_order())
        chaser = build_chaser(
            trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER_OPEN
        )

        result = chaser.run(0.5)

        assert result is not None
        assert trading.queries == [], "chaser_open 不应查询订单"

    def test_run_returns_none_when_order_never_placed(self):
        trading = FakeTradingPort()
        trading.queue_place(rejected_gtx())
        chaser = build_chaser(
            trading, FakeMarketDataPort(), PlaceOrderBehavior.CHASER, max_retry=1
        )

        assert chaser.run(0.5) is None

    def test_start_uses_given_price_only_on_first_round(self):
        trading = FakeTradingPort()
        trading.queue_place(rejected_gtx(), open_order())
        market = FakeMarketDataPort(price=0.6)
        chaser = build_chaser(
            trading, market, PlaceOrderBehavior.CHASER_OPEN, max_retry=1
        )

        result = chaser.start(first_price=0.5)

        assert result is not None
        assert placed_prices(trading) == pytest.approx([0.5, 0.6])
        assert market.price_calls == 1, "仅第二轮需要取最新价"

    def test_start_stops_once_chase_succeeds(self):
        trading = FakeTradingPort()
        trading.queue_place(open_order())
        market = FakeMarketDataPort(price=0.5)
        chaser = build_chaser(trading, market, PlaceOrderBehavior.CHASER_OPEN)

        assert chaser.start() is not None
        assert market.price_calls == 1
        assert len(trading.placed) == 1

    def test_start_gives_up_after_max_iterations(self):
        trading = FakeTradingPort()
        trading.queue_place(*[rejected_gtx() for _ in range(3)])
        market = FakeMarketDataPort(price=0.5)
        chaser = build_chaser(
            trading, market, PlaceOrderBehavior.CHASER_OPEN, max_retry=1
        )
        chaser.max_iterations = 3

        assert chaser.start() is None
        assert market.price_calls == 3, "每轮都重新取价"
