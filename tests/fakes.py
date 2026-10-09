"""测试用内存实现

所有外部依赖(ccxt 交易所、端口)都在这里有内存版本, 保证单元测试不触网、< 1 秒。
"""

from collections import deque
from typing import Any

from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    Symbol,
    SymbolInfo,
)
from smart_trader.storage.records import GridPositionRow, OrderRecord

DEFAULT_SYMBOL = Symbol(base="DOGE", quote="USDT")
DEFAULT_SYMBOL_INFO = SymbolInfo(
    symbol=DEFAULT_SYMBOL,
    tick_size=0.001,
    min_price=0.0,
    max_price=0.0,
    step_size=1.0,
    min_qty=1.0,
    max_qty=1_000_000.0,
    min_notional=5.0,
)
DEFAULT_STRATEGY_KEY = "dogeusdt_long_buy_1m"


def make_position_row(position_id: str = "POS1", **overrides: object) -> GridPositionRow:
    """构造测试用逻辑仓位行（策略参数 + 运行时状态）"""
    fields: dict[str, object] = {
        "id": position_id,
        "strategy_key": DEFAULT_STRATEGY_KEY,
        "symbol": DEFAULT_SYMBOL.ccxt(),
        "position_side": PositionSide.LONG,
        "main_order_side": OrderSide.BUY,
        "opening_quantity": 0.0,
        "opening_price": 0.0,
        "fixed_take_profit_rate": 0.01,
        "signal_min_take_profit_rate": 0.002,
    }
    fields.update(overrides)
    return GridPositionRow.model_validate(fields)


def make_order_record(
    order_id: str = "BUYexchange1",
    grid_position_id: str = "POS1",
    **overrides: object,
) -> OrderRecord:
    """构造测试用订单行"""
    fields: dict[str, object] = {
        "order_id": order_id,
        "grid_position_id": grid_position_id,
        "strategy_key": DEFAULT_STRATEGY_KEY,
        "symbol": DEFAULT_SYMBOL.ccxt(),
        "side": OrderSide.BUY,
        "type": OrderType.LIMIT,
        "price": 0.5,
        "quantity": 100.0,
        "filled_qty": 0.0,
        "status": OrderStatus.OPEN,
    }
    fields.update(overrides)
    return OrderRecord.model_validate(fields)


class FakeTradingPort:
    """内存版 TradingPort: 记录下单请求, 按队列返回预设结果"""

    def __init__(
        self,
        status: OrderStatus = OrderStatus.OPEN,
        filled_qty: float = 0.0,
        price: float | None = None,
        custom_id: str = "cid1",
    ):
        self.placed: list[OrderRequest] = []
        self.canceled: list[str] = []
        self.queries: list[str] = []
        self._default_status = status
        self._default_filled_qty = filled_qty
        self._default_price = price
        self._default_custom_id = custom_id
        self._place_results: deque[OrderResult | Exception] = deque()
        self._query_results: deque[OrderResult | None | Exception] = deque()
        self._cancel_results: deque[Exception] = deque()

    def queue_place(self, *results: OrderResult | Exception) -> None:
        self._place_results.extend(results)

    def queue_query(self, *results: OrderResult | None | Exception) -> None:
        self._query_results.extend(results)

    def queue_cancel(self, *errors: Exception) -> None:
        self._cancel_results.extend(errors)

    def place(self, request: OrderRequest) -> OrderResult:
        self.placed.append(request)
        if self._place_results:
            result = self._place_results.popleft()
            if isinstance(result, Exception):
                raise result
            return result
        return OrderResult(
            custom_id=request.custom_id,
            symbol=request.symbol,
            side=request.side,
            status=self._default_status,
            quantity=request.quantity,
            filled_qty=self._default_filled_qty,
            price=self._default_price
            if self._default_price is not None
            else request.price,
        )

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        self.queries.append(custom_id)
        if self._query_results:
            result = self._query_results.popleft()
            if isinstance(result, Exception):
                raise result
            return result
        return OrderResult(
            custom_id=self._default_custom_id,
            symbol=symbol,
            side=OrderSide.BUY,
            status=self._default_status,
            quantity=0.0,
            filled_qty=self._default_filled_qty,
        )

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        self.canceled.append(custom_id)
        if self._cancel_results:
            raise self._cancel_results.popleft()
        return OrderResult(
            custom_id=custom_id,
            symbol=symbol,
            side=OrderSide.BUY,
            status=OrderStatus.CANCELED,
            quantity=0.0,
            filled_qty=0.0,
        )


class FakeMarketDataPort:
    """内存版 MarketDataPort"""

    def __init__(
        self,
        price: float = 0.5,
        symbol_info: SymbolInfo | None = None,
        klines: list[Kline] | None = None,
    ):
        self.price = price
        self.price_calls = 0
        self.ohlcv_calls = 0
        self._symbol_info = symbol_info or DEFAULT_SYMBOL_INFO
        self._klines = klines if klines is not None else []

    def set_klines(self, klines: list[Kline]) -> None:
        self._klines = klines

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        return self._symbol_info

    def latest_price(self, symbol: Symbol) -> float:
        self.price_calls += 1
        return self.price

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        self.ohlcv_calls += 1
        return self._klines


class FakeCcxtExchange:
    """内存版 ccxt 交易所, 用于验证 BinanceSwapClient 的翻译逻辑"""

    def __init__(self) -> None:
        self.create_order_calls: list[dict[str, Any]] = []
        self.cancel_order_calls: list[dict[str, Any]] = []
        self.fetch_order_calls: list[dict[str, Any]] = []
        self.ohlcv_calls: list[dict[str, Any]] = []
        self.exchange_info_get_calls = 0
        self.create_order_result: dict[str, Any] = {
            "id": "1",
            "clientOrderId": "cid1",
            "symbol": "DOGEUSDT",
            "status": "open",
            "type": "limit",
            "side": "buy",
            "price": "0.5",
            "amount": "10",
            "filled": "0",
            "average": None,
            "info": {
                "orderId": "1",
                "status": "NEW",
                "executedQty": "0",
                "avgPrice": "0",
            },
        }
        self.fetch_order_result: dict[str, Any] | None = None
        self.cancel_order_result: dict[str, Any] = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "side": "buy",
        }
        self.create_order_error: Exception | None = None
        self.fetch_order_error: Exception | None = None
        self.cancel_order_error: Exception | None = None
        self.positions_result: list[dict[str, Any]] = []
        self.exchange_info: dict[str, Any] = {"symbols": []}
        self.ohlcv: list[list[float]] = []
        self.ticker_last: float = 0.5

    def create_order(
        self,
        symbol: str,
        type: Any = None,
        side: Any = None,
        amount: Any = None,
        price: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        if self.create_order_error is not None:
            raise self.create_order_error
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        return self.create_order_result

    def fetch_order(self, id: str, symbol: str, params: Any = None) -> dict[str, Any]:
        if self.fetch_order_error is not None:
            raise self.fetch_order_error
        self.fetch_order_calls.append({"id": id, "symbol": symbol, "params": params})
        assert self.fetch_order_result is not None, "测试需先设置 fetch_order_result"
        return self.fetch_order_result

    def cancel_order(self, id: str, symbol: str, params: Any = None) -> dict[str, Any]:
        if self.cancel_order_error is not None:
            raise self.cancel_order_error
        self.cancel_order_calls.append({"id": id, "symbol": symbol, "params": params})
        return self.cancel_order_result

    def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        return self.positions_result

    def fetch_balance(self) -> dict[str, Any]:
        return {"USDT": {"free": 100.0}}

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[list[float]]:
        self.ohlcv_calls.append(
            {"symbol": symbol, "timeframe": timeframe, "limit": limit}
        )
        return self.ohlcv

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return {"last": self.ticker_last}

    def milliseconds(self) -> int:
        return 1_700_000_000_000

    def fapiPublicGetExchangeInfo(self) -> dict[str, Any]:
        self.exchange_info_get_calls += 1
        return self.exchange_info

    def load_markets(self) -> dict[str, Any]:
        return {}

    def fetch_open_orders(
        self, symbol: str, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return []
