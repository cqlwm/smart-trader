import pytest
from pydantic import ValidationError

from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Symbol,
    TimeInForce,
)

SYMBOL = Symbol(base="DOGE", quote="USDT")


def build_limit_request(**overrides: object) -> OrderRequest:
    fields: dict[str, object] = {
        "custom_id": "cid1",
        "symbol": SYMBOL,
        "side": OrderSide.BUY,
        "type": OrderType.LIMIT,
        "quantity": 10.0,
        "price": 0.5,
        "position_side": PositionSide.LONG,
    }
    fields.update(overrides)
    return OrderRequest.model_validate(fields)


class TestOrderRequest:
    def test_limit_request_keeps_fields(self):
        request = build_limit_request(time_in_force=TimeInForce.GTX)

        assert request.custom_id == "cid1"
        assert request.price == 0.5
        assert request.time_in_force == TimeInForce.GTX

    def test_limit_request_requires_price(self):
        """限价单缺少价格时必须拒绝, 否则会以市价成交"""
        with pytest.raises(ValidationError):
            build_limit_request(price=None)

    def test_limit_request_rejects_non_positive_price(self):
        with pytest.raises(ValidationError):
            build_limit_request(price=0.0)

    def test_market_request_rejects_price(self):
        """市价单带价格属于调用方错误, 必须显式暴露"""
        with pytest.raises(ValidationError):
            build_limit_request(type=OrderType.MARKET, price=0.5)

    def test_market_request_allows_empty_price(self):
        request = build_limit_request(type=OrderType.MARKET, price=None)

        assert request.price is None

    def test_rejects_non_positive_quantity(self):
        with pytest.raises(ValidationError):
            build_limit_request(quantity=0.0)


class TestOrderResult:
    def test_filled_ratio_is_partial_fill_aware(self):
        """部分成交必须能从结果中读出来, 而不是只判断布尔值"""
        result = OrderResult(
            custom_id="cid1",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=100.0,
            filled_qty=30.0,
        )

        assert result.filled_ratio == pytest.approx(0.3), "应能计算部分成交比例"
        assert result.is_filled is False, "部分成交不能被当成完全成交"

    def test_is_filled_only_for_closed_status(self):
        result = OrderResult(
            custom_id="cid1",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.CLOSED,
            quantity=100.0,
            filled_qty=100.0,
        )

        assert result.is_filled is True

    def test_filled_ratio_is_zero_when_quantity_is_zero(self):
        result = OrderResult(
            custom_id="cid1",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=0.0,
        )

        assert result.filled_ratio == 0.0


class TestOrderStatus:
    @pytest.mark.parametrize("status", list(OrderStatus))
    def test_round_trip_from_value(self, status: OrderStatus):
        assert OrderStatus.from_value(status.value) is status

    def test_from_value_is_case_insensitive(self):
        assert OrderStatus.from_value("CLOSED") is OrderStatus.CLOSED

    def test_from_value_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            OrderStatus.from_value("canceling")

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (OrderStatus.OPEN, False),
            (OrderStatus.CLOSED, True),
            (OrderStatus.CANCELED, True),
            (OrderStatus.REJECTED, True),
            (OrderStatus.EXPIRED, True),
        ],
    )
    def test_is_finished(self, status: OrderStatus, expected: bool):
        assert status.is_finished() is expected


class TestSymbolConversions:
    def test_from_ccxt_parses_linear_swap(self):
        symbol = Symbol.from_ccxt("DOGE/USDT:USDT")

        assert symbol == Symbol(base="DOGE", quote="USDT"), (
            "线性合约应解析为 base/quote"
        )

    def test_from_ccxt_parses_plain_pair(self):
        assert Symbol.from_ccxt("doge/usdt") == Symbol(base="doge", quote="usdt")

    def test_from_ccxt_rejects_unparsable_symbol(self):
        with pytest.raises(ValueError):
            Symbol.from_ccxt("DOGEUSDT")


class TestPosition:
    def test_position_requires_known_side(self):
        with pytest.raises(ValidationError):
            Position.model_validate(
                {"symbol": SYMBOL, "side": "both", "quantity": 1.0, "entry_price": 2.0}
            )

    def test_position_accepts_position_side_enum(self):
        position = Position(
            symbol=SYMBOL, side=PositionSide.SHORT, quantity=10.0, entry_price=0.5
        )

        assert position.side is PositionSide.SHORT
