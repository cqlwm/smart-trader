"""Binance 适配器测试: 翻译规则 / 异常映射 / 双实例路由 / 构造契约 / 接缝一致性"""

from typing import Any

import ccxt  # pyright: ignore[reportMissingTypeStubs]
import pytest
from ccxt.base.types import ConstructorArgs  # pyright: ignore[reportMissingTypeStubs]

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.binance.binance_exchange import (
    BinanceExchange,
    CcxtExchange,
    build_private_ccxt,
    build_public_ccxt,
    parse_symbol_info,
    to_ccxt_order_params,
)
from smart_trader.exchange.ports import (
    AccountPort,
    Exchange,
    MarketDataPort,
    TradingPort,
)
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


def build_exchange() -> tuple[BinanceExchange, FakeCcxtExchange]:
    fake = FakeCcxtExchange()
    fake.exchange_info_response = EXCHANGE_INFO
    # 翻译逻辑测试不关心路由, 双侧注入同一 fake; 路由契约由 TestExchangeRouting 锁定
    exchange = BinanceExchange(fake, fake)
    return exchange, fake


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
        assert params.get("timeInForce") == "GTX", "GTX 即 post only"

    def test_market_order_omits_time_in_force(self):
        params = to_ccxt_order_params(build_request(price=None, type=OrderType.MARKET))

        assert "timeInForce" not in params, "市价单不支持 timeInForce"
        assert params["positionSide"] == "LONG"

    def test_raises_when_custom_id_empty(self):
        """绕过 JournaledTradingPort 直接下单并遗漏订单号, 交易所会自动生成非幂等 ID"""
        request = build_request()
        request.custom_id = ""  # 空 custom_id = 调用方绕过记账包装
        with pytest.raises(SmartTraderError) as exc_info:
            to_ccxt_order_params(request)
        assert exc_info.value.error_code is ErrorCode.ORDER_REJECTED


class TestPlace:
    def test_formats_price_and_quantity_to_exchange_precision(self):
        exchange, fake = build_exchange()

        exchange.place(build_request(price=0.0908512345, quantity=100.6))

        call = fake.create_order_calls[0]
        assert call["price"] == pytest.approx(0.09085), "价格必须按 tick_size 取整"
        assert call["amount"] == 100, "数量必须按 step_size 取整"
        assert call["symbol"] == "DOGE/USDC"
        assert call["side"] == "buy"

    def test_passes_client_id_and_position_side_via_params(self):
        """下单参数必须经 params 原样透传(newClientOrderId/positionSide/timeInForce)"""
        exchange, fake = build_exchange()

        exchange.place(build_request(time_in_force=TimeInForce.GTX))

        assert fake.create_order_calls[0]["params"] == {
            "newClientOrderId": "cid1",
            "positionSide": "LONG",
            "timeInForce": "GTX",
        }

    def test_returns_domain_result_with_filled_quantity(self):
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "closed",
            "price": "0.09085",
            "amount": "100",
            "filled": "100",
            "average": "0.0909",
            "side": "buy",
        }

        result = exchange.place(build_request())

        assert result.status is OrderStatus.CLOSED
        assert result.filled_qty == 100.0
        assert result.avg_price == pytest.approx(0.0909)
        assert result.is_filled is True

    def test_closed_order_without_filled_field_reports_full_quantity(self):
        """终态(完全成交)订单若未回传成交量, 必须按委托量兜底, 否则会漏记持仓"""
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "closed",
            "amount": "100",
            "side": "buy",
        }

        result = exchange.place(build_request())

        assert result.filled_qty == 100.0, "完全成交的订单成交量必然等于委托量"

    def test_canceled_order_without_filled_field_reports_zero(self):
        """已撤销订单未回传成交量时应视为 0, 不能兜底成委托量"""
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "amount": "100",
            "side": "buy",
        }

        result = exchange.place(build_request())

        assert result.filled_qty == 0.0

    def test_uses_exchange_returned_client_id(self):
        """交易所可能改写客户端订单号, 结果必须以交易所返回为准"""
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "x-rewritten",
            "status": "open",
            "price": "0.09085",
            "amount": "100",
            "filled": "0",
            "side": "buy",
        }

        result = exchange.place(build_request())

        assert result.custom_id == "x-rewritten"

    def test_rejects_order_below_min_notional_without_calling_exchange(self):
        exchange, fake = build_exchange()

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request(price=0.09085, quantity=50.0))

        assert exc.value.error_code is ErrorCode.ORDER_NOTIONAL_TOO_SMALL
        assert fake.create_order_calls == [], "校验失败不应发起真实请求"

    def test_raises_when_exchange_returns_order_without_client_id(self):
        exchange, fake = build_exchange()
        fake.create_order_result = {"status": "open", "amount": "100"}

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request())

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_raises_order_response_invalid_on_unknown_status(self):
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "canceling",
            "amount": "100",
        }

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request())

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_raises_order_response_invalid_on_unparseable_side(self):
        """交易所回传了 side 但无法解析为 buy/sell, 属于数据完整性问题, 必须失败而非静默兜底"""
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "amount": "100",
            "side": "unknown",
        }

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request())

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_raises_order_response_invalid_when_exchange_omits_side(self):
        """交易所未回传 side 属响应数据不完整, 必须失败而非猜测方向"""
        exchange, fake = build_exchange()
        fake.create_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "amount": "100",
        }

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request())

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
        exchange, fake = build_exchange()
        fake.create_order_error = error

        with pytest.raises(SmartTraderError) as exc:
            exchange.place(build_request())

        assert exc.value.error_code is expected_code
        assert exc.value.is_transient is transient


class TestQuery:
    def test_returns_domain_result(self):
        exchange, fake = build_exchange()
        fake.fetch_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "price": "0.09085",
            "amount": "100",
            "filled": "30",
            "side": "buy",
        }

        result = exchange.query("cid1", SYMBOL)

        assert result is not None
        assert result.filled_qty == 30.0, "部分成交量必须透出"
        assert result.status is OrderStatus.OPEN
        assert fake.fetch_order_calls[0]["params"]["origClientOrderId"] == "cid1"

    def test_raises_order_response_invalid_on_unparseable_side(self):
        """查询返回了无法解析的 side 时不得静默兜底(查询路径无兜底方向)"""
        exchange, fake = build_exchange()
        fake.fetch_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "amount": "100",
            "side": "unknown",
        }

        with pytest.raises(SmartTraderError) as exc:
            exchange.query("cid1", SYMBOL)

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_raises_order_response_invalid_when_exchange_omits_side(self):
        """查询返回缺少 side 时同样视为响应不完整, 不得静默返回 None 方向"""
        exchange, fake = build_exchange()
        fake.fetch_order_result = {
            "clientOrderId": "cid1",
            "status": "open",
            "amount": "100",
        }

        with pytest.raises(SmartTraderError) as exc:
            exchange.query("cid1", SYMBOL)

        assert exc.value.error_code is ErrorCode.ORDER_RESPONSE_INVALID

    def test_returns_none_when_order_not_found(self):
        """订单不存在属于可预期情况, 不应向上抛异常"""
        exchange, fake = build_exchange()
        fake.fetch_order_error = ccxt.OrderNotFound("Unknown order sent.")

        assert exchange.query("cid1", SYMBOL) is None


class TestCancel:
    def test_returns_domain_result(self):
        exchange, fake = build_exchange()
        fake.cancel_order_result = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "price": "0.09085",
            "amount": "100",
            "filled": "0",
            "side": "buy",
        }

        result = exchange.cancel("cid1", SYMBOL)

        assert result is not None
        assert result.status is OrderStatus.CANCELED
        assert fake.cancel_order_calls[0]["params"]["origClientOrderId"] == "cid1"

    def test_returns_none_when_order_not_found(self):
        exchange, fake = build_exchange()
        fake.cancel_order_error = ccxt.OrderNotFound("Unknown order sent.")

        assert exchange.cancel("cid1", SYMBOL) is None


class TestMarketData:
    def test_symbol_info_is_cached(self):
        exchange, fake = build_exchange()

        exchange.symbol_info(SYMBOL)
        exchange.symbol_info(SYMBOL)

        assert fake.exchange_info_get_calls == 1, (
            "交易规则应缓存, 不能每次下单都拉取"
        )

    def test_symbol_info_cache_is_per_symbol(self):
        """缓存按交易对区分: 同一交易对命中缓存, 不同交易对各自解析"""
        exchange, fake = build_exchange()
        fake.exchange_info_response = {
            "symbols": [
                EXCHANGE_INFO["symbols"][0],
                {**EXCHANGE_INFO["symbols"][0], "symbol": "BTCUSDC"},
            ]
        }

        exchange.symbol_info(SYMBOL)
        exchange.symbol_info(SYMBOL)
        exchange.symbol_info(Symbol(base="BTC", quote="USDC"))
        exchange.symbol_info(SYMBOL)

        assert fake.exchange_info_get_calls == 2, (
            "同一交易对命中缓存, 不同交易对各拉取一次"
        )

    def test_latest_price_returns_float(self):
        exchange, fake = build_exchange()
        fake.ticker_last = 1.2345

        assert exchange.latest_price(SYMBOL) == pytest.approx(1.2345)

    def test_fetch_ohlcv_keeps_only_finished_flag_semantic(self):
        exchange, fake = build_exchange()
        fake.ohlcv = [[1_600_000_000_000, 1.0, 2.0, 0.5, 1.5, 100.0]]

        klines = exchange.fetch_ohlcv(SYMBOL, "1m", limit=1)

        assert len(klines) == 1
        assert klines[0].close == 1.5
        assert klines[0].finished is True, "历史K线必须标记为已收盘"

    def test_fetch_ohlcv_returns_empty_for_non_positive_limit(self):
        exchange, fake = build_exchange()
        fake.ohlcv = [[1_600_000_000_000, 1.0, 2.0, 0.5, 1.5, 100.0]]

        assert exchange.fetch_ohlcv(SYMBOL, "1m", limit=0) == []
        assert fake.ohlcv_calls == [], "limit 非法时不应请求交易所"

    def test_fetch_ohlcv_passes_limit_as_limit_not_since(self):
        """回归: limit 曾被位置传参进 ccxt fetch_ohlcv 的 since 槽(since=limit
        → startTime≈1970), 导致永远拉到数据集最老的K线(2020)而非最新K线"""
        exchange, fake = build_exchange()
        fake.ohlcv = [[1_600_000_000_000, 1.0, 2.0, 0.5, 1.5, 100.0]]

        klines = exchange.fetch_ohlcv(SYMBOL, "1m", limit=2)

        call = fake.ohlcv_calls[0]
        assert call["since"] is None, (
            "limit 不得落入 ccxt fetch_ohlcv 的 since 位置参数"
        )
        assert call["limit"] == 2
        assert len(klines) == 1 and klines[0].close == 1.5


class TestAccount:
    def test_positions_maps_to_domain_objects(self):
        exchange, fake = build_exchange()
        fake.positions_result = [
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

        positions = exchange.positions(SYMBOL)

        assert len(positions) == 1, "已平仓(无方向)的持仓不应返回"
        assert positions[0].symbol == Symbol(base="DOGE", quote="USDC")
        assert positions[0].side is PositionSide.LONG
        assert positions[0].quantity == 10.0
        assert positions[0].entry_price == pytest.approx(0.09085)

    def test_balance_returns_free_amount(self):
        exchange, _ = build_exchange()

        assert exchange.balance("usdt") == 100.0


class TestExchangeRouting:
    """双实例路由契约: 行情走公共实例, 交易/账户走私有实例"""

    @staticmethod
    def build_split_exchange() -> tuple[BinanceExchange, FakeCcxtExchange, FakeCcxtExchange]:
        public, private = FakeCcxtExchange(), FakeCcxtExchange()
        public.exchange_info_response = EXCHANGE_INFO
        exchange = BinanceExchange(private, public)
        return exchange, public, private

    def test_market_data_methods_use_public_exchange(self):
        exchange, public, private = self.build_split_exchange()

        exchange.latest_price(SYMBOL)
        exchange.fetch_ohlcv(SYMBOL, "1m", limit=2)

        assert len(public.ohlcv_calls) == 1, "行情方法必须走公共实例"
        assert private.ohlcv_calls == [], "行情不得打到私有(交易)实例"

    def test_trading_methods_use_private_exchange(self):
        exchange, public, private = self.build_split_exchange()

        exchange.place(build_request())

        assert len(private.create_order_calls) == 1, "下单必须走私有实例"
        assert public.create_order_calls == [], "交易不得打到公共(行情)实例"


class TestCcxtConstruction:
    """raw ccxt 实例构造期契约: 凭据与 demo 开关只进私有实例, 双实例构造即预载市场"""

    @staticmethod
    def build_with_factory(is_test: bool) -> list[tuple[ConstructorArgs, FakeCcxtExchange]]:
        created: list[tuple[ConstructorArgs, FakeCcxtExchange]] = []

        def factory(options: ConstructorArgs) -> FakeCcxtExchange:
            fake = FakeCcxtExchange()
            created.append((options, fake))
            return fake

        build_private_ccxt("key123", "secret456", is_test=is_test, raw_factory=factory)
        build_public_ccxt(raw_factory=factory)
        return created

    def test_public_exchange_carries_no_credentials(self):
        created = self.build_with_factory(is_test=False)

        private = next(options for options, _ in created if "apiKey" in options)
        public = next(options for options, _ in created if "apiKey" not in options)

        assert private.get("apiKey") == "key123" and private.get("secret") == "secret456"
        assert public == {"options": {"defaultType": "future"}}, (
            "公共实例只做行情, 不得携带任何凭据"
        )

    def test_is_test_enables_demo_only_on_private_exchange(self):
        created = self.build_with_factory(is_test=True)

        private = next(fake for options, fake in created if "apiKey" in options)
        public = next(fake for options, fake in created if "apiKey" not in options)

        assert private.demo_enabled is True, "demo 开关只作用于私有实例"
        assert public.demo_enabled is False
        assert private.load_markets_calls == 1 and public.load_markets_calls == 1, (
            "双实例构造时都预载市场, 启动即暴露网络问题"
        )


class TestSeamConformance:
    """CcxtExchange 接缝一致性: 真实 ccxt 漂移在测试层拦截"""

    def test_real_ccxt_satisfies_seam_protocol(self):
        """真实 ccxt.binance 实例必须具备接缝 Protocol 声明的全部方法

        @runtime_checkable 只校验方法存在(拦截 ccxt 改名/移除方法);
        签名漂移由行为测试与 FakeCcxtExchange 的显式继承锁定。
        构造 ccxt 实例不触网。
        """
        assert isinstance(ccxt.binance(), CcxtExchange), (
            "ccxt 上游改名/移除了接缝声明的方法, 适配器需要跟进"
        )

    def test_binance_exchange_satisfies_exchange_and_ports(self):
        """BinanceExchange 显式满足 Exchange 组合接口与三个端口"""
        exchange = BinanceExchange(FakeCcxtExchange(), FakeCcxtExchange())

        assert isinstance(exchange, Exchange), "应满足 Exchange 组合接口"
        assert isinstance(exchange, MarketDataPort), "应满足 MarketDataPort"
        assert isinstance(exchange, TradingPort), "应满足 TradingPort"
        assert isinstance(exchange, AccountPort), "应满足 AccountPort"
