from unittest.mock import Mock

import pytest

from smart_trader.client.binance_chaser_order_v2 import (
    LatestPriceProvider,
    LimitOrderChaserV2,
)
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import OrderSide, PlaceOrderBehavior, Symbol, SymbolInfo

SYMBOL = Symbol(base="DOGE", quote="USDT")
TICK_SIZE = 0.001
GTX_REJECTED = Exception('{"code":-5022,"msg":"挂单会被立即成交"}')


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
    client.place_order_v2.side_effect = [
        RuntimeError("网络超时"),
        open_order(),
    ]

    assert chaser.chase_open_only(0.5) is not None
    assert placed_prices(client) == pytest.approx([0.5, 0.499])


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
    assert chaser.custom_id is not None
    client.query_order.assert_not_called()


def test_chase_returns_false_when_order_never_placed():
    chaser, client = build_chaser(FakePriceProvider(0.5), PlaceOrderBehavior.CHASER)
    client.place_order_v2.side_effect = GTX_REJECTED

    assert chaser.chase(0.5) is False
    assert chaser.custom_id is None


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
