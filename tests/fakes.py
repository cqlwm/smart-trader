"""测试用内存实现

所有外部依赖(ccxt 交易所、端口)都在这里有内存版本, 保证单元测试不触网、< 1 秒。
"""

import json
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from smart_trader.exchange.binance.binance_exchange import (
    CcxtClientOrderIdParams,
    CcxtCoinBalance,
    CcxtExchange,
    CcxtOpenOrder,
    CcxtOrder,
    CcxtOrderParams,
    CcxtPosition,
    CcxtTicker,
)
from smart_trader.exchange.ports import AccountPort, MarketDataPort, TradingPort
from smart_trader.model import (
    Kline,
    MarketSignal,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    SignalDirection,
    Symbol,
    SymbolInfo,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord
from smart_trader.strategy.entry_gate import EntryContext, EntryGate
from smart_trader.strategy.grid_position import GridPosition, load_positions
from smart_trader.strategy.signal import Signal

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


DEFAULT_SIGNAL_DATETIME = "2026-01-01 00:00:00"


def make_market_signal(
    direction: SignalDirection = SignalDirection.LONG,
    datetime: str = DEFAULT_SIGNAL_DATETIME,
) -> MarketSignal:
    """构造行情信号快照"""
    return MarketSignal(direction=direction, datetime=datetime)


def make_kline(
    symbol: Symbol | None = None,
    *,
    timeframe: str = "1m",
    finished: bool = True,
    close: float = 1.5,
    timestamp: int = 1_700_000_000_000,
) -> Kline:
    """构造单根 K 线（open/high/low 固定, 满足载荷分发类测试）"""
    return Kline(
        symbol=symbol if symbol is not None else DEFAULT_SYMBOL,
        timeframe=timeframe,
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=100.0,
        timestamp=timestamp,
        finished=finished,
    )


def make_ws_kline_message(
    stream: str = "dogeusdt@kline_1m",
    *,
    finished: bool = True,
    **kline_overrides: Any,
) -> str:
    """构造币安 ws K 线推送的原始 JSON 字符串"""
    kline: dict[str, Any] = {
        "t": 1_700_000_000_000,
        "o": "0.1",
        "h": "0.2",
        "l": "0.05",
        "c": "0.15",
        "v": "100",
        "x": finished,
    }
    kline.update(kline_overrides)
    return json.dumps({"stream": stream, "data": {"k": kline}})


def make_position(
    position_id: str = "POS1",
    *,
    orders: list[OrderRecord] | None = None,
    **overrides: object,
) -> GridPosition:
    """构造逻辑仓位领域对象（字段默认值与 make_position_row 对齐）

    `orders` 为仅关键字参数, 避免与 `**overrides` 争用同一名字
    """
    row = make_position_row(position_id, **overrides)
    return GridPosition.from_row(row, orders if orders is not None else [])


def make_entry_context(
    positions: list[GridPosition] | None = None,
    close: float | None = 0.5,
    market_signal: MarketSignal | None = None,
) -> EntryContext:
    """构造入场闸门上下文"""
    return EntryContext(
        positions=positions if positions is not None else [],
        close=close,
        market_signal=market_signal,
    )


class RecordingGate(EntryGate):
    """记录 allow 调用顺序的闸门，返回预设结果（用于短路与优先级断言）"""

    def __init__(self, result: bool, name: str) -> None:
        self._result = result
        self._name = name
        self.calls: list[str] = []

    def allow(self, ctx: EntryContext) -> bool:
        self.calls.append(self._name)
        return self._result


class FakeSignal(Signal):
    """预设立场序列的信号（不跑 talib, 供策略层测试保持毫秒级）"""

    def __init__(
        self,
        directions: list[SignalDirection] | None = None,
        datetime: str = DEFAULT_SIGNAL_DATETIME,
    ) -> None:
        self._directions = list(directions or [SignalDirection.LONG])
        self._datetime = datetime
        self.evaluate_calls = 0

    def evaluate(self, klines: Any) -> MarketSignal:
        index = min(self.evaluate_calls, len(self._directions) - 1)
        self.evaluate_calls += 1
        return MarketSignal(
            direction=self._directions[index], datetime=self._datetime
        )


def make_klines_df(
    closes: list[float],
    start: str = "2026-01-01 00:00:00",
    interval_seconds: int = 60,
) -> Any:
    """构造确定性的 OHLCV DataFrame（收盘价序列驱动, 无随机数）

    @param closes 收盘价序列; open=high=low=close
    """
    import pandas as pd

    datetimes = [
        (
            datetime.strptime(start, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
            + timedelta(seconds=index * interval_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        for index in range(len(closes))
    ]
    return pd.DataFrame(
        {
            "datetime": pd.Series(datetimes, dtype="str"),
            "open": pd.Series(closes, dtype="float64"),
            "high": pd.Series(closes, dtype="float64"),
            "low": pd.Series(closes, dtype="float64"),
            "close": pd.Series(closes, dtype="float64"),
            "volume": pd.Series([1.0] * len(closes), dtype="float64"),
            "finished": pd.Series([True] * len(closes), dtype="boolean"),
        }
    )


def make_exchange_position(
    quantity: float,
    *,
    side: PositionSide = PositionSide.LONG,
    symbol: Symbol | None = None,
) -> Position:
    """构造交易所侧持仓领域对象（AccountPort.positions 的返回元素）"""
    return Position(
        symbol=symbol if symbol is not None else DEFAULT_SYMBOL,
        side=side,
        quantity=quantity,
        entry_price=0.5,
    )


class FakeAccountPort(AccountPort):
    """内存版 AccountPort: 可编程持仓快照, 记录查询调用"""

    def __init__(self) -> None:
        self.positions_calls = 0
        self.balance_calls = 0
        self._positions: list[Position] = []
        self._positions_error: Exception | None = None

    def set_positions(self, *positions: Position) -> None:
        self._positions = list(positions)

    def fail_positions(self, error: Exception) -> None:
        """让后续 positions 查询持续抛出该异常（模拟账户端口故障）"""
        self._positions_error = error

    def balance(self, coin: str) -> float:
        self.balance_calls += 1
        return 100.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        self.positions_calls += 1
        if self._positions_error is not None:
            raise self._positions_error
        return list(self._positions)


class LedgerMirroringAccountPort(AccountPort):
    """镜像账本活跃仓位的账户端口: 表达"本地 = 交易所, 无外部偏差"测试前提

    从账本(而非策略内存)派生持仓 —— 策略构造期的启动对账发生在本地仓位
    加载之后, 账本是彼时唯一可用的仓位来源; 运行期账本与内存同步落库,
    两者一致。需要模拟"外部手动平仓/加仓"偏差的测试应显式传入
    `FakeAccountPort`。
    """

    def __init__(self, journal: OrderJournal, strategy_key: str, symbol: Symbol) -> None:
        self.positions_calls = 0
        self._journal = journal
        self._strategy_key = strategy_key
        self._symbol = symbol

    def balance(self, coin: str) -> float:
        return 100.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        self.positions_calls += 1
        return [
            Position(
                symbol=self._symbol,
                side=item.position_side,
                quantity=item.quantity,
                entry_price=item.price,
            )
            for item in load_positions(self._journal, self._strategy_key)
            if item.quantity > 0
        ]


class FakeTradingPort(TradingPort):
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


class FakeMarketDataPort(MarketDataPort):
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


class FakeCcxtExchange(CcxtExchange):
    """内存版 raw ccxt 交易所, 显式继承 `CcxtExchange` 接缝 Protocol

    pyright 校验本类签名与接缝一致（Step 17 教训: Fake 签名漂移会掩盖
    适配器缺陷）。fixture 属性故意保留 CcxtOrder 全量形状(total=False):
    负路径测试需要注入缺键/非法值的残缺响应, 键名拼写由 pyright 检查。
    """

    def __init__(self) -> None:
        self.create_order_calls: list[dict[str, Any]] = []
        self.cancel_order_calls: list[dict[str, Any]] = []
        self.fetch_order_calls: list[dict[str, Any]] = []
        self.ohlcv_calls: list[dict[str, Any]] = []
        self.exchange_info_get_calls = 0
        self.demo_enabled = False
        self.load_markets_calls = 0
        self.create_order_result: CcxtOrder = {
            "clientOrderId": "cid1",
            "status": "open",
            "side": "buy",
            "price": "0.5",
            "amount": "10",
            "filled": "0",
            "average": None,
        }
        self.fetch_order_result: CcxtOrder | None = None
        # 与币安 fapi 撤单响应同形: origQty(executedQty)始终回传, 适配器按完整契约解析
        self.cancel_order_result: CcxtOrder = {
            "clientOrderId": "cid1",
            "status": "canceled",
            "side": "buy",
            "price": "0.5",
            "amount": "10",
            "filled": "0",
        }
        self.create_order_error: Exception | None = None
        self.fetch_order_error: Exception | None = None
        self.cancel_order_error: Exception | None = None
        self.positions_result: list[CcxtPosition] = []
        self.exchange_info_response: dict[str, Any] = {"symbols": []}
        self.ohlcv: list[list[float]] = []
        self.ticker_last: float = 0.5

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: CcxtOrderParams | None = None,
    ) -> CcxtOrder:
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

    def fetch_order(
        self, id: str, symbol: str, params: CcxtClientOrderIdParams | None = None
    ) -> CcxtOrder:
        if self.fetch_order_error is not None:
            raise self.fetch_order_error
        self.fetch_order_calls.append({"id": id, "symbol": symbol, "params": params})
        assert self.fetch_order_result is not None, "测试需先设置 fetch_order_result"
        return self.fetch_order_result

    def cancel_order(
        self, id: str, symbol: str, params: CcxtClientOrderIdParams | None = None
    ) -> CcxtOrder:
        if self.cancel_order_error is not None:
            raise self.cancel_order_error
        self.cancel_order_calls.append({"id": id, "symbol": symbol, "params": params})
        return self.cancel_order_result

    def fetch_positions(
        self, symbols: list[str] | None = None
    ) -> list[CcxtPosition]:
        return self.positions_result

    def fetch_balance(self) -> dict[str, CcxtCoinBalance]:
        return {"USDT": {"free": 100.0}}

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Any = None,
        limit: int | None = None,
        params: Any = None,
    ) -> list[list[float]]:
        # 签名与 ccxt 原生一致: 第三个位置参数是 since 而非 limit
        self.ohlcv_calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "since": since,
                "limit": limit,
            }
        )
        return self.ohlcv

    def fetch_ticker(self, symbol: str) -> CcxtTicker:
        return {"last": self.ticker_last}

    def milliseconds(self) -> int:
        return 1_700_000_000_000

    def fapiPublicGetExchangeInfo(self) -> dict[str, Any]:
        self.exchange_info_get_calls += 1
        return self.exchange_info_response

    def enable_demo_trading(self, enable: bool) -> None:
        self.demo_enabled = enable

    def load_markets(self) -> dict[str, Any]:
        self.load_markets_calls += 1
        return {}

    def fetch_open_orders(self, symbol: str) -> list[CcxtOpenOrder]:
        return []
