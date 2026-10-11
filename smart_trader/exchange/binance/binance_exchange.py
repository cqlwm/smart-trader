"""Binance 适配器

职责边界（见 .memory/01-system-architecture.md）:
1. `CcxtExchange` Protocol: 全项目唯一的 Protocol, 描述 raw ccxt 实例的最小类型化
   接缝（第三方形状, 测试可换内存实现）, `@runtime_checkable` + 真实实例一致性
   测试拦截"ccxt 改名/移除方法"的漂移;
2. `build_public_ccxt` / `build_private_ccxt`: raw 实例构造（选项/demo 开关/预载市场）,
   `raw_factory` 注入缝供测试打桩;
3. `BinanceExchange`: 领域对象 ⇄ ccxt 参数/响应的翻译, 实现 `Exchange` 三端口,
   对 `CcxtExchange` 类型实例的每个调用点受静态校验;
4. ccxt 异常 → SmartTraderError + ErrorCode 的翻译。

不做的事: 追单编排、重试策略、价格递进(属于 execution 层)。
"""

from collections.abc import Callable
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

import ccxt  # pyright: ignore[reportMissingTypeStubs]
from ccxt.base.types import ConstructorArgs  # pyright: ignore[reportMissingTypeStubs]

from smart_trader import log
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import Exchange
from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Symbol,
    SymbolInfo,
    timeframe_seconds,
)

logger = log.getLogger(__name__)

# ---------------------------------------------------------------------------
# ccxt 方言词汇表 (TypedDict)
#
# 入站订单结构 (CcxtOrder) total=False: 交易所返回不可信, "哪些键缺失属响应不完整"
# 的运行时契约由 to_order_result 的防御式解析强制并有测试锁定, 类型层只负责键名
# 拼写与消费面文档。其余结构的键按适配器的实际消费方式声明必填性。
# 值类型保留 float | str —— 交易所数字字段可能以字符串回传。
# ---------------------------------------------------------------------------


class CcxtOrder(TypedDict, total=False):
    """ccxt 统一订单结构中本项目消费的字段 (下单/查询/撤单共用)

    status / clientOrderId / side 运行时必填: 缺失或非法 → ORDER_RESPONSE_INVALID;
    其余容忍缺失, 由 _to_float 兜底。
    """

    status: str
    clientOrderId: str
    side: str
    amount: float | str
    filled: float | str
    price: float | str | None
    average: float | str | None


class CcxtOpenOrder(TypedDict, total=False):
    """fetch_open_orders 条目中本项目消费的字段"""

    clientOrderId: str
    id: str


class CcxtPosition(TypedDict):
    """ccxt 统一持仓结构中本项目消费的字段

    symbol 必填; side 缺失/为 None = 已平仓占位(适配器跳过)。
    """

    symbol: str
    side: NotRequired[str | None]
    contracts: NotRequired[float | str | None]
    entryPrice: NotRequired[float | str | None]


class CcxtTicker(TypedDict):
    """ccxt 统一行情结构中本项目消费的字段"""

    last: float


class CcxtCoinBalance(TypedDict):
    """单币种余额中本项目消费的字段"""

    free: float | str


class CcxtOrderParams(TypedDict):
    """下单 params 出站参数"""

    newClientOrderId: str
    positionSide: str
    timeInForce: NotRequired[str]


class CcxtClientOrderIdParams(TypedDict):
    """按 clientOrderId 查询/撤单的出站参数 (币安 origClientOrderId)"""

    origClientOrderId: str


@runtime_checkable
class CcxtExchange(Protocol):
    """
    raw ccxt 交易所的最小类型化接缝 —— 全项目唯一的 Protocol

    第三方形状: 真实 ccxt 实例无法继承我们的 ABC, 因此用结构化 Protocol 声明
    适配器消费的最小表面, 测试用内存实现结构化满足。方法签名必须与 ccxt 原生
    一致（如 fetch_ohlcv 第三位置参数是 since 而非 limit）, 否则位置传参错位会
    被类型检查掩盖; `fapiPublicGetExchangeInfo` 是币安隐式端点名 —— 接缝只说
    ccxt 方言, 领域翻译由 BinanceExchange 完成。

    isinstance(ccxt.binance(), CcxtExchange) 一致性测试拦截"ccxt 改名/移除方法"
    的漂移（运行时仅校验方法存在, 签名漂移由行为测试锁定）。
    """

    def load_markets(self) -> dict[str, Any]: ...

    def fapiPublicGetExchangeInfo(self) -> dict[str, Any]: ...

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: CcxtOrderParams | None = None,
    ) -> CcxtOrder: ...

    def fetch_order(
        self, id: str, symbol: str, params: CcxtClientOrderIdParams | None = None
    ) -> CcxtOrder: ...

    def cancel_order(
        self, id: str, symbol: str, params: CcxtClientOrderIdParams | None = None
    ) -> CcxtOrder: ...

    def fetch_positions(
        self, symbols: list[str] | None = None
    ) -> list[CcxtPosition]: ...

    def fetch_balance(self) -> dict[str, CcxtCoinBalance]: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Any = None,
        limit: int | None = None,
        params: Any = None,
    ) -> list[list[float]]: ...

    def fetch_ticker(self, symbol: str) -> CcxtTicker: ...

    def fetch_open_orders(self, symbol: str) -> list[CcxtOpenOrder]: ...

    def milliseconds(self) -> int: ...


def parse_symbol_info(exchange_info: dict[str, Any], symbol: Symbol) -> SymbolInfo:
    """
    从交易所 exchangeInfo 中解析交易规则
    @param exchange_info: 币安 /fapi/v1/exchangeInfo 响应
    @param symbol: 交易对
    """
    for entry in exchange_info.get("symbols", []):
        if entry.get("symbol") != symbol.binance():
            continue

        filters: dict[str, float] = {}
        for filter_info in entry.get("filters", []):
            filter_type = filter_info.get("filterType")
            if filter_type == "PRICE_FILTER":
                filters["tick_size"] = float(filter_info["tickSize"])
                filters["min_price"] = float(filter_info["minPrice"])
                filters["max_price"] = float(filter_info["maxPrice"])
            elif filter_type == "LOT_SIZE":
                filters["step_size"] = float(filter_info["stepSize"])
                filters["min_qty"] = float(filter_info["minQty"])
                filters["max_qty"] = float(filter_info["maxQty"])
            elif filter_type in ("MIN_NOTIONAL", "NOTIONAL"):
                filters["min_notional"] = float(filter_info["notional"])

        required = (
            "tick_size",
            "min_price",
            "max_price",
            "step_size",
            "min_qty",
            "max_qty",
            "min_notional",
        )
        missing = [name for name in required if name not in filters]
        if missing:
            raise SmartTraderError(
                ErrorCode.SYMBOL_INFO_UNAVAILABLE,
                f"{symbol.binance()} 缺少交易规则过滤器: {', '.join(missing)}",
            )

        return SymbolInfo(symbol=symbol, **filters)  # pyright: ignore[reportArgumentType]

    raise SmartTraderError(
        ErrorCode.SYMBOL_INFO_UNAVAILABLE,
        f"交易所未返回 {symbol.binance()} 的交易规则",
    )


def to_ccxt_order_params(request: OrderRequest) -> CcxtOrderParams:
    """
    领域下单请求 → ccxt 下单参数

    前置条件: request.custom_id 已由 JournaledTradingPort 分配合法值, 空串意味着
    调用方绕过了记账包装, 交易所会自动生成非幂等 ID, 破坏"先登记后下单"契约。
    """
    if not request.custom_id:
        raise SmartTraderError(
            ErrorCode.ORDER_REJECTED,
            "custom_id 为空: 下单必须经过 JournaledTradingPort 分配订单号",
        )
    params: CcxtOrderParams = {
        "newClientOrderId": request.custom_id,
        "positionSide": request.position_side.value.upper(),
    }
    if request.type == OrderType.LIMIT and request.time_in_force is not None:
        params["timeInForce"] = request.time_in_force.value
    return params


def to_order_result(order: CcxtOrder, symbol: Symbol) -> OrderResult:
    """
    ccxt 订单结构 → 领域订单结果
    @param order: ccxt 统一订单结构
    @param symbol: 交易对(交易所未回传时可确认)
    @raise SmartTraderError: 响应缺少必需字段, 或状态/方向无法解析
    """
    raw_status = order.get("status")
    try:
        status = OrderStatus.from_value(str(raw_status))
    except (ValueError, AttributeError) as e:
        raise SmartTraderError(
            ErrorCode.ORDER_RESPONSE_INVALID,
            f"无法解析订单状态: {raw_status}",
        ) from e

    custom_id = order.get("clientOrderId")
    if not custom_id:
        raise SmartTraderError(
            ErrorCode.ORDER_RESPONSE_INVALID,
            f"订单响应缺少 clientOrderId: {order}",
        )

    raw_side = order.get("side")
    if isinstance(raw_side, str) and raw_side.lower() in ("buy", "sell"):
        side = OrderSide(raw_side.lower())
    else:
        # 交易所必回传方向; 缺失或非法都属响应数据不完整, 不得猜测兜底
        raise SmartTraderError(
            ErrorCode.ORDER_RESPONSE_INVALID,
            f"无法解析订单方向: {raw_side}",
        )

    quantity = _to_float(order.get("amount")) or 0.0
    filled_qty = _to_float(order.get("filled")) or 0.0
    if status.is_closed() and not filled_qty:
        # 完全成交的订单成交量必然等于委托量; 交易所未回传时兜底,
        # 否则策略层会认为该订单没有成交而漏记持仓
        filled_qty = quantity

    return OrderResult(
        custom_id=custom_id,
        symbol=symbol,
        side=side,
        status=status,
        quantity=quantity,
        filled_qty=filled_qty,
        price=_to_float(order.get("price")),
        avg_price=_to_float(order.get("average")),
    )


def _to_float(value: Any) -> float | None:
    """把交易所返回的数字/字符串安全转为 float, 空值与 0 归一化为 None"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _translate_error(error: Exception, action: str) -> SmartTraderError:
    """
    ccxt 异常 → SmartTraderError
    @param error: ccxt 异常
    @param action: 出错的操作名, 用于日志与消息
    """
    message = f"{action}失败: {error}"
    if isinstance(error, ccxt.OrderNotFillable):
        # post only 单会立即成交被交易所拒绝, 换价重试即可
        return SmartTraderError(ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY, message)
    if isinstance(error, ccxt.InsufficientFunds):
        return SmartTraderError(ErrorCode.INSUFFICIENT_FUNDS, message)
    if isinstance(error, ccxt.OrderNotFound):
        return SmartTraderError(ErrorCode.ORDER_NOT_FOUND, message)
    if isinstance(error, (ccxt.RateLimitExceeded, ccxt.DDoSProtection)):
        return SmartTraderError(ErrorCode.EXCHANGE_RATE_LIMITED, message)
    if isinstance(error, (ccxt.NetworkError, ccxt.ExchangeNotAvailable)):
        return SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, message)
    if isinstance(error, ccxt.InvalidOrder):
        return SmartTraderError(ErrorCode.ORDER_REJECTED, message)
    if isinstance(error, ccxt.ExchangeError):
        return SmartTraderError(ErrorCode.EXCHANGE_ERROR, message)
    return SmartTraderError(ErrorCode.EXCHANGE_ERROR, message)


RawCcxtFactory = Callable[[ConstructorArgs], Any]


def build_public_ccxt(raw_factory: RawCcxtFactory = ccxt.binance) -> CcxtExchange:
    """
    公共实例: 只承载行情, 不携带任何凭据; 构造即预载市场
    @param raw_factory: raw ccxt 构造缝, 测试注入内存实现
    """
    raw = raw_factory(ConstructorArgs(options={"defaultType": "future"}))
    raw.load_markets()
    return raw


def build_private_ccxt(
    api_key: str,
    api_secret: str,
    is_test: bool = False,
    raw_factory: RawCcxtFactory = ccxt.binance,
) -> CcxtExchange:
    """
    私有实例: 承载交易与账户; is_test=True 时切到 demo 交易; 构造即预载市场
    @param api_key: API key
    @param api_secret: API secret
    @param is_test: 是否切换 demo 交易
    @param raw_factory: raw ccxt 构造缝, 测试注入内存实现
    """
    raw = raw_factory(
        ConstructorArgs(
            apiKey=api_key,
            secret=api_secret,
            options={
                "defaultType": "future",
                # ccxt 默认关闭该校正, 本机时钟略快时签名时间戳会超出币安 1s 限制(-1021)
                "adjustForTimeDifference": True,
            },
        )
    )
    if is_test:
        raw.enable_demo_trading(True)
    raw.load_markets()
    return raw


class BinanceExchange(Exchange):
    """Binance U 本位合约适配器, 显式实现 MarketDataPort / TradingPort / AccountPort

    只依赖 `CcxtExchange` 最小接口做翻译, 不直接触达 ccxt;
    生产组装见 `create_binance_exchange`(双实例按能力分流)。
    """

    def __init__(
        self, private_exchange: CcxtExchange, public_exchange: CcxtExchange
    ) -> None:
        self._private_exchange = private_exchange
        self._public_exchange = public_exchange
        self._symbol_info_cache: dict[str, SymbolInfo] = {}

    # ---- MarketDataPort ----

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        key = symbol.binance()
        cached = self._symbol_info_cache.get(key)
        if cached is None:
            cached = parse_symbol_info(
                self._public_exchange.fapiPublicGetExchangeInfo(), symbol
            )
            self._symbol_info_cache[key] = cached
        return cached

    def latest_price(self, symbol: Symbol) -> float:
        ticker = self._public_exchange.fetch_ticker(symbol.ccxt())
        return float(ticker["last"])

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        if limit < 1:
            return []

        now_ms = self._public_exchange.milliseconds()
        timeframe_ms = self._parse_timeframe_ms(timeframe)
        klines: list[Kline] = []
        # limit 必须关键字传参: ccxt fetch_ohlcv 第三个位置参数是 since,
        # 位置传参会把 limit 当成 since(≈1970), 恒拉数据集最老的K线
        for ohlcv in self._public_exchange.fetch_ohlcv(
            symbol.ccxt(), timeframe, limit=limit
        ):
            timestamp = int(ohlcv[0])
            klines.append(
                Kline(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=float(ohlcv[1]),
                    high=float(ohlcv[2]),
                    low=float(ohlcv[3]),
                    close=float(ohlcv[4]),
                    volume=float(ohlcv[5]),
                    finished=timestamp + timeframe_ms <= now_ms,
                )
            )
        return klines

    # ---- TradingPort ----

    def place(self, request: OrderRequest) -> OrderResult:
        symbol_info = self.symbol_info(request.symbol)
        quantity = symbol_info.format_qty(request.quantity)
        symbol_info.check_qty(quantity)

        price = request.price
        if price is not None:
            price = symbol_info.format_price(price)
            symbol_info.check_notional(price, quantity)

        params = to_ccxt_order_params(request)
        try:
            order = self._private_exchange.create_order(
                symbol=request.symbol.ccxt(),
                type=request.type.value,
                side=request.side.value,
                amount=quantity,
                price=price,
                params=params,
            )
        except Exception as e:
            logger.debug(
                "下单失败: symbol: %s, type: %s, side: %s, quantity: %s, price: %s, params: %s",
                request.symbol.binance(),
                request.type.value,
                request.side.value,
                quantity,
                price,
                params,
            )
            raise _translate_error(e, "下单") from e

        logger.debug("下单返回: %s", order)
        return to_order_result(order, request.symbol)

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        try:
            order = self._private_exchange.fetch_order(
                id="",
                symbol=symbol.ccxt(),
                params={"origClientOrderId": custom_id},
            )
        except ccxt.OrderNotFound:
            logger.info("订单不存在: %s", custom_id)
            return None
        except Exception as e:
            raise _translate_error(e, "查询订单") from e

        logger.debug("查询订单返回: %s", order)
        return to_order_result(order, symbol)

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        try:
            order = self._private_exchange.cancel_order(
                id="",
                symbol=symbol.ccxt(),
                params={"origClientOrderId": custom_id},
            )
        except ccxt.OrderNotFound:
            logger.info("撤单时订单不存在: %s", custom_id)
            return None
        except Exception as e:
            raise _translate_error(e, "撤单") from e

        logger.debug("撤单返回: %s", order)
        return to_order_result(order, symbol)

    # ---- AccountPort ----

    def balance(self, coin: str) -> float:
        balance = self._private_exchange.fetch_balance()
        return float(balance[coin.upper()]["free"])

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        raw_positions = (
            self._private_exchange.fetch_positions([symbol.ccxt()])
            if symbol is not None
            else self._private_exchange.fetch_positions()
        )
        positions: list[Position] = []
        for raw in raw_positions:
            side = raw.get("side")
            quantity = _to_float(raw.get("contracts"))
            if side not in ("long", "short") or quantity is None:
                # 已平仓或无方向的占位记录
                continue
            positions.append(
                Position(
                    symbol=Symbol.from_ccxt(raw["symbol"]),
                    side=PositionSide(side),
                    quantity=quantity,
                    entry_price=_to_float(raw.get("entryPrice")) or 0.0,
                )
            )
        return positions

    # ---- 非端口能力 ----

    def cancel_open_orders(self, symbol: Symbol) -> list[OrderResult]:
        """撤销该交易对所有挂单（非端口能力, 仅 demo 使用）"""
        raw_orders = self._private_exchange.fetch_open_orders(symbol.ccxt())
        results: list[OrderResult] = []
        for raw in raw_orders:
            custom_id = raw.get("clientOrderId") or raw.get("id")
            if not custom_id:
                continue
            canceled = self.cancel(custom_id, symbol)
            if canceled is not None:
                results.append(canceled)
        return results

    # ---- 内部工具 ----

    def _parse_timeframe_ms(self, timeframe: str) -> int:
        """
        解析周期字符串(如 1m/1h)为毫秒
        @param timeframe: 周期
        @raise SmartTraderError: 非法周期
        """
        try:
            return timeframe_seconds(timeframe) * 1000
        except ValueError as error:
            raise SmartTraderError(
                ErrorCode.EXCHANGE_ERROR, f"不支持的K线周期: {timeframe}"
            ) from error


def create_binance_exchange(
    api_key: str, api_secret: str, is_test: bool = False
) -> BinanceExchange:
    """生产组装入口: 私有实例承载交易/账户(is_test 切 demo), 公共实例只做行情零凭据"""
    return BinanceExchange(
        private_exchange=build_private_ccxt(api_key, api_secret, is_test),
        public_exchange=build_public_ccxt(),
    )
