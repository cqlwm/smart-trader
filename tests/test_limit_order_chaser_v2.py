from unittest.mock import Mock

import ccxt  # pyright: ignore[reportMissingTypeStubs]
import pytest

from smart_trader.client.binance_chaser_order_v2 import (
    LatestPriceProvider,
    LimitOrderChaserV2,
)
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import OrderSide, PlaceOrderBehavior, Symbol, SymbolInfo

SYMBOL = Symbol(base="DOGE", quote="USDT")
TICK_SIZE = 0.001
# {"code":-5022,"msg":"由于订单无法以挂单方式成交，此挂单将被拒绝"}
GTX_REJECTED = ccxt.OrderNotFillable("挂单会被立即成交")


class FakePriceProvider:
    def __init__(self, price: float):
        self.price = price
        self.calls = 0

    def latest_price(self, symbol: Symbol) -> float:
        self.calls += 1
        return self.price


def build_chaser(
    provider: LatestPriceProvider,
    behavior: PlaceOrderBehavior,
    side: OrderSide = OrderSide.BUY,
    max_retry: int = 3,
    wait_timeout: float = 0.0,
    custom_id: str = "cid1",
) -> tuple[LimitOrderChaserV2, Mock]:
    client = Mock(spec=ExSwapClient)
    client.symbol_info.return_value = SymbolInfo(
        symbol=SYMBOL,
        tick_size=TICK_SIZE,
        min_price=0.0,
        max_price=0.0,
        step_size=1.0,
        min_qty=1.0,
        max_qty=1_000_000.0,
    )
    chaser = LimitOrderChaserV2(
        client=client,
        price_provider=provider,
        symbol=SYMBOL,
        side=side,
        quantity=100.0,
        position_side="LONG",
        place_order_behavior=behavior,
        custom_id=custom_id,
        max_retry=max_retry,
        poll_interval=0.0,
        wait_timeout=wait_timeout,
    )
    return chaser, client


def open_order(**kwargs: object) -> dict[str, object]:
    return {"status": "open", "info": {"executedQty": "0"}, **kwargs}


def filled_order(executed_qty: str = "100") -> dict[str, object]:
    return {"status": "closed", "info": {"executedQty": executed_qty}}


def placed_prices(client: Mock) -> list[float]:
    return [call.kwargs["price"] for call in client.place_order_v2.call_args_list]


@pytest.mark.parametrize(
    ("side", "expected_prices"),
    [
        (OrderSide.BUY, [0.5, 0.499, 0.498]),
        (OrderSide.SELL, [0.5, 0.501, 0.502]),
    ],
)
def test_chase_open_only_steps_price_toward_maker(
    side: OrderSide, expected_prices: list[float]
):
    """挂单失败时向更容易成为 maker 的方向递进：买单降价，卖单涨价"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN, side=side
    )
    client.place_order_v2.side_effect = [
        GTX_REJECTED,
        GTX_REJECTED,
        open_order(),
    ]

    assert chaser.chase_open_only(0.5) is not None
    assert placed_prices(client) == pytest.approx(expected_prices)


def test_chase_open_only_returns_none_after_max_retry():
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = GTX_REJECTED

    assert chaser.chase_open_only(0.5) is None
    assert client.place_order_v2.call_count == chaser.max_retry


def test_chase_open_only_keeps_going_on_unexpected_error():
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.query_order.return_value = None  # 超时后查无此单, 才继续递进重试
    client.place_order_v2.side_effect = [
        RuntimeError("网络超时"),
        open_order(),
    ]

    assert chaser.chase_open_only(0.5) is not None
    assert placed_prices(client) == pytest.approx([0.5, 0.499])


def test_chase_open_only_stops_on_invalid_order():
    """交易所确定性拒绝(如名义价值不足)时立刻失败, 不递进价格重试"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.query_order.return_value = None  # 查无此单: 确定性拒绝而非重复ID
    client.place_order_v2.side_effect = ccxt.InvalidOrder("notional too small")

    with pytest.raises(ccxt.InvalidOrder):
        chaser.chase_open_only(0.5)

    assert client.place_order_v2.call_count == 1


def test_chase_open_only_stops_on_notional_error():
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = SmartTraderError(
        ErrorCode.ORDER_NOTIONAL_TOO_SMALL, "名义价值不足"
    )

    with pytest.raises(SmartTraderError):
        chaser.chase_open_only(0.5)

    assert client.place_order_v2.call_count == 1


def test_chase_closed_waits_until_filled():
    """等待期间成交则直接返回 True，不撤单"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER, wait_timeout=5.0
    )
    client.query_order.side_effect = [open_order(), open_order(), filled_order()]

    assert chaser.chase_closed("cid1") is True
    client.cancel.assert_not_called()


def test_chase_closed_returns_false_when_order_canceled():
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER, wait_timeout=5.0
    )
    client.query_order.return_value = {
        "status": "canceled",
        "info": {"executedQty": "0"},
    }

    assert chaser.chase_closed("cid1") is False


def test_chase_closed_cancels_and_rechecks_when_timeout():
    """超时未成交：撤单后再校验一次，撤单前刚好成交仍算成功"""
    chaser, client = build_chaser(FakePriceProvider(0.5), PlaceOrderBehavior.CHASER)
    client.query_order.return_value = filled_order()

    assert chaser.chase_closed("cid1") is True
    client.cancel.assert_called_once_with("cid1", SYMBOL)


def test_chase_closed_returns_false_when_still_unfilled_after_cancel():
    chaser, client = build_chaser(FakePriceProvider(0.5), PlaceOrderBehavior.CHASER)
    client.query_order.return_value = open_order()

    assert chaser.chase_closed("cid1") is False
    client.cancel.assert_called_once_with("cid1", SYMBOL)


def test_chase_returns_true_without_waiting_for_chaser_open():
    """chaser_open 只下单，不等待成交"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.return_value = open_order()

    assert chaser.chase(0.5) is True
    assert chaser.custom_id == "cid1"
    client.query_order.assert_not_called()


def test_chase_returns_false_when_order_never_placed():
    chaser, client = build_chaser(FakePriceProvider(0.5), PlaceOrderBehavior.CHASER)
    client.place_order_v2.side_effect = GTX_REJECTED

    assert chaser.chase(0.5) is False
    assert client.place_order_v2.call_count == chaser.max_retry


def test_start_uses_first_price_only_on_first_round():
    provider = FakePriceProvider(0.6)
    chaser, client = build_chaser(provider, PlaceOrderBehavior.CHASER_OPEN, max_retry=1)
    chaser.first_price = 0.5
    client.place_order_v2.side_effect = [GTX_REJECTED, open_order()]

    assert chaser.run() is True
    assert placed_prices(client) == pytest.approx([0.5, 0.6])
    assert provider.calls == 1


def test_start_stops_once_chase_succeeds():
    provider = FakePriceProvider(0.5)
    chaser, client = build_chaser(provider, PlaceOrderBehavior.CHASER_OPEN)
    client.place_order_v2.return_value = open_order()

    assert chaser.run() is True
    assert provider.calls == 1
    assert client.place_order_v2.call_count == 1


def test_start_gives_up_after_max_iterations():
    provider = FakePriceProvider(0.5)
    chaser, client = build_chaser(provider, PlaceOrderBehavior.CHASER_OPEN, max_retry=1)
    chaser.max_iterations = 3
    client.place_order_v2.side_effect = GTX_REJECTED

    assert chaser.run() is False
    assert provider.calls == 3


# ------------------------------------------------- custom_id 复用与孤儿单防御


def test_chase_reuses_custom_id_across_retries():
    """所有递进重试复用同一 custom_id: 提交超时后重试不会产生重复订单"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.query_order.return_value = None
    client.place_order_v2.side_effect = [GTX_REJECTED, RuntimeError("超时"), open_order()]

    assert chaser.chase_open_only(0.5) == "cid1"
    custom_ids = [
        call.kwargs["custom_id"] for call in client.place_order_v2.call_args_list
    ]
    assert custom_ids == ["cid1", "cid1", "cid1"], "重试必须复用同一 custom_id"


def test_timeout_with_existing_order_takes_over_without_reorder():
    """提交超时但订单实际已到达交易所: 查单确认后直接接管, 不重复下单"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = RuntimeError("超时")
    client.query_order.return_value = open_order()

    assert chaser.place_order_gtx(0.5) == "cid1"
    assert client.place_order_v2.call_count == 1, "订单已存在时不应重复下单"
    client.query_order.assert_called_once_with("cid1", SYMBOL)


def test_invalid_order_with_existing_order_takes_over():
    """InvalidOrder 但订单已存在(上次请求实际成功): 查单确认后接管而非报错"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = ccxt.InvalidOrder("duplicate client order id")
    client.query_order.return_value = open_order()

    assert chaser.place_order_gtx(0.5) == "cid1"
    client.query_order.assert_called_once_with("cid1", SYMBOL)


def test_invalid_order_without_existing_order_still_raises():
    """InvalidOrder 且查无此单: 确定性拒绝, 停止追单"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = ccxt.InvalidOrder("bad params")
    client.query_order.return_value = None

    with pytest.raises(ccxt.InvalidOrder):
        chaser.place_order_gtx(0.5)


def test_order_exists_query_failure_treated_as_missing():
    """恢复性查单失败: 视为不存在, 交由下一轮重试再判定 (不吞掉异常也不误接管)"""
    chaser, client = build_chaser(
        FakePriceProvider(0.5), PlaceOrderBehavior.CHASER_OPEN
    )
    client.place_order_v2.side_effect = RuntimeError("超时")
    client.query_order.side_effect = RuntimeError("查单也超时")

    assert chaser.place_order_gtx(0.5) is None, "查单失败时不应误判为订单已存在"
