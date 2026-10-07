from unittest.mock import Mock

import pytest

from smart_trader.client.binance_client import BinanceSwapClient, parse_symbol_info
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import OrderSide, Symbol, SymbolInfo

SYMBOL = Symbol(base="DOGE", quote="USDC")
EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "DOGEUSDC",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "tickSize": "0.000010",
                    "minPrice": "0.000010",
                    "maxPrice": "200",
                },
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": "1",
                    "minQty": "1",
                    "maxQty": "30000000",
                },
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    ]
}


def build_client() -> BinanceSwapClient:
    client = BinanceSwapClient.__new__(BinanceSwapClient)
    client.exchange = Mock()
    client.exchange_info = EXCHANGE_INFO
    return client


def build_symbol_info(min_notional: float = 5.0) -> SymbolInfo:
    return SymbolInfo(
        symbol=SYMBOL,
        tick_size=0.00001,
        min_price=0.00001,
        max_price=200.0,
        step_size=1.0,
        min_qty=1.0,
        max_qty=30_000_000.0,
        min_notional=min_notional,
    )


def test_parse_symbol_info_reads_min_notional():
    symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

    assert symbol_info.min_notional == 5.0, "应解析交易所的 MIN_NOTIONAL 最小值"


def test_parse_symbol_info_raises_without_min_notional():
    exchange_info = {
        "symbols": [
            {
                "symbol": "DOGEUSDC",
                "filters": EXCHANGE_INFO["symbols"][0]["filters"][:2],  # type: ignore[index]
            }
        ]
    }

    with pytest.raises(ValueError):
        parse_symbol_info(exchange_info, SYMBOL)


def test_check_notional_raises_when_below_minimum():
    symbol_info = build_symbol_info()

    with pytest.raises(SmartTraderError) as exc:
        symbol_info.check_notional(price=0.09085, quantity=50.0)

    assert exc.value.error_code == ErrorCode.ORDER_NOTIONAL_TOO_SMALL


def test_check_notional_allows_value_at_minimum():
    build_symbol_info().check_notional(price=0.5, quantity=10.0)


def test_place_order_rejects_order_below_min_notional():
    client = build_client()

    with pytest.raises(SmartTraderError) as exc:
        client.place_order_v2(
            custom_id="cid1",
            symbol=SYMBOL,
            order_side=OrderSide.BUY,
            quantity=50.0,
            price=0.09085,
            position_side="LONG",
        )

    assert exc.value.error_code == ErrorCode.ORDER_NOTIONAL_TOO_SMALL
    client.exchange.create_order.assert_not_called()


def test_place_order_submits_when_notional_is_enough():
    client = build_client()
    client.exchange.create_order.return_value = {"clientOrderId": "cid1"}

    order = client.place_order_v2(
        custom_id="cid1",
        symbol=SYMBOL,
        order_side=OrderSide.BUY,
        quantity=100.0,
        price=0.09085,
        position_side="LONG",
    )

    assert order == {"clientOrderId": "cid1"}
    assert client.exchange.create_order.call_args.kwargs["amount"] == 100.0