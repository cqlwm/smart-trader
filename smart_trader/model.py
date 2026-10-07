import builtins
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, model_validator

from smart_trader.exceptions import ErrorCode, SmartTraderError


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

    def reversal(self):
        return OrderSide.SELL if self == OrderSide.BUY else OrderSide.BUY

    def to_int(self):
        """
        返回订单方向的整数表示
        @return:
            买单时,返回1
            卖单时,返回-1
        """
        return 1 if self == OrderSide.BUY else -1

    def extremum_fun(self):
        """
        返回极值函数
        @return:
            买单时,极值函数为min
            卖单时,极值函数为max
        """

        return min if self == OrderSide.BUY else max

    def compare_fun(self, and_eq: bool = False):
        """
        返回比较函数
        @param and_eq: 是否包含等于情况
        @return:
            买单时,比较函数为大于函数
            卖单时,比较函数为小于函数
        """
        if and_eq:
            return (
                builtins.float.__ge__
                if self == OrderSide.BUY
                else builtins.float.__le__
            )
        return builtins.float.__gt__ if self == OrderSide.BUY else builtins.float.__lt__


# order status
# statuses: dict = {
#     'NEW': 'open',
#     'PARTIALLY_FILLED': 'open',
#     'ACCEPTED': 'open',
#     'FILLED': 'closed',
#     'CANCELED': 'canceled',
#     'CANCELLED': 'canceled',
#     'PENDING_CANCEL': 'canceling',  # currently unused
#     'REJECTED': 'rejected',
#     'EXPIRED': 'expired',
#     'EXPIRED_IN_MATCH': 'expired',
# }


class OrderStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @classmethod
    def from_value(cls, value: "str | OrderStatus") -> "OrderStatus":
        """
        解析交易所统一订单状态, 未知状态抛出 ValueError
        @param value: 交易所返回的状态字符串或枚举本身
        """
        if isinstance(value, OrderStatus):
            return value
        for status in cls:
            if status.value == value.lower():
                return status
        raise ValueError(f"未知订单状态: {value}")

    def is_open(self) -> bool:
        """订单在交易所仍可成交"""
        return self is OrderStatus.OPEN

    def is_closed(self) -> bool:
        """订单已完全成交"""
        return self is OrderStatus.CLOSED

    def is_finished(self) -> bool:
        """订单进入终态, 不会再有新的成交"""
        return self in (
            OrderStatus.CLOSED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(Enum):
    """订单有效期策略; GTX 表示只做 maker(post only)"""

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"


class PlaceOrderBehavior(Enum):
    CHASER = "chaser"  # 追单且成交
    CHASER_OPEN = "chaser_open"  # 追单只下单
    NORMAL = "normal"  # 常规


class Symbol(BaseModel):
    base: str
    quote: str

    def ccxt(self):
        return f"{self.base}/{self.quote}".upper()

    def binance(self):
        return f"{self.base}{self.quote}".upper()

    def binance_ws_sub_kline(self, timeframe: str):
        return f"{self.binance().lower()}@kline_{timeframe}"

    def simple(self):
        return f"{self.base}{self.quote}"

    @classmethod
    def from_ccxt(cls, ccxt_symbol: str) -> "Symbol":
        """
        解析 ccxt 统一交易对(如 DOGE/USDT 或线性合约 DOGE/USDT:USDT)
        @param ccxt_symbol: ccxt 格式交易对
        """
        base_with_quote = ccxt_symbol.split(":")[0]
        parts = base_with_quote.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"无法解析 ccxt 交易对: {ccxt_symbol}")
        return cls(base=parts[0], quote=parts[1])


class OrderRequest(BaseModel):
    """下单请求(领域对象), 是策略层与交易所适配器之间的唯一契约"""

    custom_id: str
    symbol: Symbol
    side: OrderSide
    type: OrderType
    quantity: float
    price: float | None = None
    position_side: PositionSide
    time_in_force: TimeInForce | None = None

    @model_validator(mode="after")
    def _validate_price(self) -> "OrderRequest":
        if self.quantity <= 0:
            raise ValueError(f"下单数量必须大于 0, 但 got {self.quantity}")

        if self.type == OrderType.LIMIT:
            if self.price is None or self.price <= 0:
                raise ValueError(f"限价单必须指定大于 0 的价格, 但 got {self.price}")
        elif self.price is not None:
            raise ValueError(f"市价单不能指定价格, 但 got {self.price}")

        return self


class Position(BaseModel):
    """持仓(领域对象)"""

    symbol: Symbol
    side: PositionSide
    quantity: float
    entry_price: float = 0.0


class OrderResult(BaseModel):
    """订单结果(领域对象), place/query/cancel 的统一返回"""

    custom_id: str
    symbol: Symbol
    side: OrderSide | None = None
    status: OrderStatus
    quantity: float = 0.0
    filled_qty: float = 0.0
    price: float | None = None
    avg_price: float | None = None

    @property
    def filled_ratio(self) -> float:
        """已成交比例; 委托数量为 0 时返回 0"""
        if self.quantity <= 0:
            return 0.0
        return self.filled_qty / self.quantity

    @property
    def is_filled(self) -> bool:
        """是否完全成交"""
        return self.status.is_closed()


class SymbolInfo(BaseModel):
    symbol: Symbol
    tick_size: float
    min_price: float
    max_price: float
    step_size: float
    min_qty: float
    max_qty: float
    min_notional: float = 6.0

    def _precision(self, number: float | str):
        return Decimal(str(number)).normalize().as_tuple().exponent * -1

    def price_precision(self):
        return self._precision(self.tick_size)

    def qty_precision(self):
        return self._precision(self.step_size)

    def format_precision(self, value: float | str, precision: float | str):
        decimal_value = Decimal(str(value))
        format_str = f"{{:.{precision}f}}"
        return float(format_str.format(decimal_value))

    def format_price(self, price: float | str):
        """价格按 tick_size 四舍五入到可交易精度"""
        return self.format_precision(price, self.price_precision())

    def format_qty(self, qty: float | str):
        """数量按 step_size 向下取整, 保证不超出可用余额与交易所上限"""
        decimal_value = Decimal(str(qty))
        quantum = Decimal(str(self.step_size)).normalize()
        return float(decimal_value.quantize(quantum, rounding=ROUND_DOWN))

    def check_qty(self, quantity: float) -> None:
        """校验数量是否满足交易所最小下单量"""
        if quantity < self.min_qty:
            raise SmartTraderError(
                ErrorCode.ORDER_QUANTITY_TOO_SMALL,
                f"{self.symbol.binance()} 下单数量 {quantity} 低于交易所最小值 {self.min_qty}",
            )

    def check_notional(self, price: float, quantity: float) -> None:
        notional = price * quantity
        if notional < self.min_notional:
            raise SmartTraderError(
                ErrorCode.ORDER_NOTIONAL_TOO_SMALL,
                f"{self.symbol.binance()} 订单名义价值 {notional} 低于交易所最小值 {self.min_notional}",
            )


class Kline:
    def __init__(
        self,
        symbol: Symbol,
        timeframe: str,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: int,
        finished: bool,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp
        self.datetime = (
            datetime.fromtimestamp(timestamp / 1000, tz=UTC)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        self.finished = finished

    def to_dict(self) -> dict[str, Any]:
        return {
            "datetime": self.datetime,
            # 'timestamp': self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "finished": self.finished,
        }
