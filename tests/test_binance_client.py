from typing import Any

import ccxt  # pyright: ignore[reportMissingTypeStubs]
import pytest

from smart_trader.client.binance_client import (
    BinanceSwapClient,
    parse_symbol_info,
    to_ccxt_order_params,
)
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    Symbol,
    TimeInForce,
)
from tests.fakes import FakeCcxtExchange

SYMBOL = Symbol(base="DOGE", quote="USDC")

EXCHANGE_INFO: dict[str, Any] = {
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


def build_client() -> tuple[BinanceSwapClient, FakeCcxtExchange]:
    client = BinanceSwapClient.__new__(BinanceSwapClient)
    exchange = FakeCcxtExchange()
    exchange.exchange_info = EXCHANGE_INFO
    client._exchange = exchange
    client._exchange_info_cache = exchange.exchange_info
    return client, exchange


def build_request(
    price: float | None = 0.09085,
    quantity: float = 100.0,
    type: OrderType = OrderType.LIMIT,
    time_in_force: TimeInForce | None = None,
) -> OrderRequest:
    return OrderRequest(
        custom_id="cid1",
        symbol=SYMBOL,
        side=OrderSide.BUY,
        type=type,
        quantity=quantity,
        price=price,
        position_side=PositionSide.LONG,
        time_in_force=time_in_force,
    )


class TestParseSymbolInfo:
    def test_reads_filters(self):
        symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

        assert symbol_info.min_notional == 5.0, "应解析交易所的 MIN_NOTIONAL 最小值"
        assert symbol_info.tick_size == 0.00001
        assert symbol_info.step_size == 1

    def test_raises_symbol_info_unavailable_without_min_notional(self):
        exchange_info = {
            "symbols": [
                {
                    "symbol": "DOGEUSDC",
                    "filters": EXCHANGE_INFO["symbols"][0]["filters"][:2],
                }
            ]
        }

        with pytest.raises(SmartTraderError) as exc:
            parse_symbol_info(exchange_info, SYMBOL)

        assert exc.value.error_code is ErrorCode.SYMBOL_INFO_UNAVAILABLE

    def test_raises_symbol_info_unavailable_for_unknown_symbol(self):
        with pytest.raises(SmartTraderError) as exc:
            parse_symbol_info(EXCHANGE_INFO, Symbol(base="BTC", quote="USDC"))

        assert exc.value.error_code is ErrorCode.SYMBOL_INFO_UNAVAILABLE


class TestSymbolInfoFormatting:
    def test_format_qty_rounds_down(self):
        """数量必须向下取整: 向上取整会导致余额不足被拒单"""
        symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

        assert symbol_info.format_qty(100.9) == 100.0

    def test_format_qty_keeps_exact_multiple(self):
        symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

        assert symbol_info.format_qty(100.0) == 100.0

    def test_format_price_rounds_to_tick(self):
        symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

        assert symbol_info.format_price(0.090858999) == pytest.approx(0.09086)

    def test_check_qty_rejects_below_minimum(self):
        symbol_info = parse_symbol_info(EXCHANGE_INFO, SYMBOL)

        with pytest.raises(SmartTraderError) as exc:
            symbol_info.check_qty(0.5)

        assert exc.value.error_code is ErrorCode.ORDER_QUANTITY_TOO_SMALL

    def test_check_qty_allows_exactly_minimum(self):
        parse_symbol_info(EXCHANGE_INFO, SYMBOL).check_qty(1.0)


class TestToCcxtOrderParams:
    def test_limit_order_carries_client_id_position_side_and_gtx(self):
        params = to_ccxt_order_params(build_request(time_in_force=TimeInForce.GTX))

        assert params["newClientOrderId"] == "cid1"
        assert params["positionSide"] == "LONG", "双向持仓模式必须显式声明持仓方向"
        assert params["timeInForce"] == "GTX", "GTX 即 post only"

    def test_market_order_omits_time_in_force(self):
        params = to_ccxt_order_params(build_request(price=None, type=OrderType.MARKET))

        assert "timeInForce" not in params, "市价单不支持 timeInForce"
        assert params["positionSide"] == "LONG"


class TestPlace:
    def test_formats_price_and_quantity_to_exchange_precision(self):
        client, exchange = build_client()

        client.place(build_request(price=0.0908512345, quantity=100.6))

        call = exchange.create_order_calls[0]
        assert call["price"] == pytest.approx(0.09085), "价格必须按 tick_size 取整"
        assert call["amount"] == 100, "数量必须按 step_size 取整"
        assert call["symbol"] == "DOGE/USDC"
        assert call["side"] == "buy"

    def test_returns_domain_result_with_filled_quantity(self):
        client, exchange = build_client()
        exchange.create_order_result = {
            "clientOrderId": "cid1",
            "status": "closed",
            "price": "0.09085",
            "amount": "100",
            "filled": "100",
            "average": "0.0909",
            "side": "buy",
        }

        result = client.place(build_request())

        assert result.status is OrderStatus.CLOSED
        assert result.filled_qty == 100.0
        assert result.avg_price == pytest.approx(0.0909)
        assert result.is_filled is True

    def test_closed_order_without_filled_field_reports_full_quantity(self):
        """终态(完全成交)订单若未回传成交量, 必须按委托量兜底, 否则会漏记持仓"""
        client, exchange = build_client()
        exchange.create_order_result = {
            "clientOrderId": "cid1",
            "status": "closed",
            "amount": "100",
        }

        result = client.place(build_request())

        assert result.filled_qty == 100.0, "完全成交的订单成交量必然等于委托量"

    def test_canceled_order_without_filled_field_reports_zero(self):
        """已撤销订单未回传成交量时应视为 0, 不能兜底成委托量"""
        client, exchange = build_client()
        exchange.create_order_result = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "amount": "100",
        }

        result = client.place(build_request())

        assert result.filled_qty == 0.0

    def test_uses_exchange_returned_client_id(self):
        """交易所可能改写客户端订单号, 结果必须以交易所返回为准"""
        client, exchange = build_client()
        exchange.create_order_result = {
            "clientOrderId": "x-rewritten",
            "status": "open",
            "price": "0.09085",
            "amount": "100",
            "filled": "0",
        }

        result = client.place(build_request())

        assert result.custom_id == "x-rewritten"

    def test_rejects_order_below_min_notional_without_calling_exchange(self):
        client, exchange = build_client()

        with pytest.raises(SmartTraderError) as exc:
            client.place(build_request(price=0.09085, quantity=50.0))

        assert exc.value.error_code is ErrorCode.ORDER_NOTIONAL_TOO_SMALL
        assert exchange.create_order_calls == [], "校验失败不应发起真实请求"

    def test_raises_when_exchange_returns_order_without_client_id(self):
        client, exchange = build_client()
        exchange.create_order_result = {"status": "open", "amount": "100"}

        with pytest.raises(SmartTraderError) as exc:
            client.place(build_request())

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_raises_order_response_invalid_on_unknown_status(self):
        client, exchange = build_client()
        exchange.create_order_result = {
            "clientOrderId": "cid1",
            "status": "canceling",
            "amount": "100",
        }

        with pytest.raises(SmartTraderError) as exc:
            client.place(build_request())

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    @pytest.mark.parametrize(
        ("error", "expected_code", "transient"),
        [
            (
                ccxt.OrderNotFillable("post only 会被立即成交"),
                ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY,
                False,
            ),
            (ccxt.InvalidOrder("参数错误"), ErrorCode.ORDER_REJECTED, False),
            (ccxt.InsufficientFunds("余额不足"), ErrorCode.INSUFFICIENT_FUNDS, False),
            (
                ccxt.RateLimitExceeded("too many requests"),
                ErrorCode.EXCHANGE_RATE_LIMITED,
                True,
            ),
            (ccxt.RequestTimeout("timeout"), ErrorCode.EXCHANGE_UNAVAILABLE, True),
            (ccxt.ExchangeError("未知错误"), ErrorCode.EXCHANGE_ERROR, False),
        ],
    )
    def test_translates_ccxt_errors(
        self, error: Exception, expected_code: ErrorCode, transient: bool
    ):
        client, exchange = build_client()
        exchange.create_order_error = error

        with pytest.raises(SmartTraderError) as exc:
            client.place(build_request())

        assert exc.value.error_code is expected_code
        assert exc.value.is_transient is transient


class TestQuery:
    def test_returns_domain_result(self):
        client, exchange = build_client()
        exchange.fetch_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "price": "0.09085",
            "amount": "100",
            "filled": "30",
            "info": {"origClientOrderId": "cid1"},
        }

        result = client.query("cid1", SYMBOL)

        assert result is not None
        assert result.filled_qty == 30.0, "部分成交量必须透出"
        assert result.status is OrderStatus.OPEN
        assert exchange.fetch_order_calls[0]["params"]["origClientOrderId"] == "cid1"

    def test_returns_none_when_order_not_found(self):
        """订单不存在属于可预期情况, 不应向上抛异常"""
        client, exchange = build_client()
        exchange.fetch_order_error = ccxt.OrderNotFound("Unknown order sent.")

        assert client.query("cid1", SYMBOL) is None


class TestCancel:
    def test_returns_domain_result(self):
        client, exchange = build_client()
        exchange.cancel_order_result = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "price": "0.09085",
            "amount": "100",
            "filled": "0",
        }

        result = client.cancel("cid1", SYMBOL)

        assert result is not None
        assert result.status is OrderStatus.CANCELED
        assert exchange.cancel_order_calls[0]["params"]["origClientOrderId"] == "cid1"

    def test_returns_none_when_order_not_found(self):
        client, exchange = build_client()
        exchange.cancel_order_error = ccxt.OrderNotFound("Unknown order sent.")

        assert client.cancel("cid1", SYMBOL) is None


class TestMarketData:
    def test_symbol_info_is_cached(self):
        client, exchange = build_client()
        client._exchange_info_cache = None  # pyright: ignore[reportPrivateUsage]

        client.symbol_info(SYMBOL)
        client.symbol_info(SYMBOL)

        assert exchange.exchange_info_get_calls == 1, (
            "交易规则应缓存, 不能每次下单都拉取"
        )

    def test_latest_price_returns_float(self):
        client, exchange = build_client()
        exchange.ticker_last = 1.2345

        assert client.latest_price(SYMBOL) == pytest.approx(1.2345)

    def test_fetch_ohlcv_keeps_only_finished_flag_semantic(self):
        client, exchange = build_client()
        exchange.ohlcv = [[1_600_000_000_000, 1.0, 2.0, 0.5, 1.5, 100.0]]

        klines = client.fetch_ohlcv(SYMBOL, "1m", limit=1)

        assert len(klines) == 1
        assert klines[0].close == 1.5
        assert klines[0].finished is True, "历史K线必须标记为已收盘"

    def test_fetch_ohlcv_returns_empty_for_non_positive_limit(self):
        client, exchange = build_client()
        exchange.ohlcv = [[1_600_000_000_000, 1.0, 2.0, 0.5, 1.5, 100.0]]

        assert client.fetch_ohlcv(SYMBOL, "1m", limit=0) == []
        assert exchange.ohlcv_calls == [], "limit 非法时不应请求交易所"


class TestAccount:
    def test_positions_maps_to_domain_objects(self):
        client, exchange = build_client()
        exchange.positions_result = [
            {
                "symbol": "DOGE/USDC:USDC",
                "side": "long",
                "contracts": 10.0,
                "entryPrice": 0.09085,
            },
            {
                "symbol": "DOGE/USDC:USDC",
                "side": None,
                "contracts": 0.0,
                "entryPrice": 0.0,
            },
        ]

        positions = client.positions(SYMBOL)

        assert len(positions) == 1, "已平仓(无方向)的持仓不应返回"
        assert positions[0].symbol == Symbol(base="DOGE", quote="USDC")
        assert positions[0].side is PositionSide.LONG
        assert positions[0].quantity == 10.0
        assert positions[0].entry_price == pytest.approx(0.09085)

    def test_balance_returns_free_amount(self):
        client, _ = build_client()

        assert client.balance("usdt") == 100.0
