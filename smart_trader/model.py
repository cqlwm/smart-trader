import builtins
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

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
    """下单请求(领域对象), 是策略层与交易所适配器之间的唯一契约

    custom_id 由底层记账包装(JournaledTradingPort)在 place 前分发并覆盖,
    策略层调用方无需填写；仅在绕过记账包装直连 TradingPort 的场景(如 demo/测试)
    才需要显式指定。交易所适配器禁止收到空 custom_id。
    """

    custom_id: str = ""
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
    side: OrderSide
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


_TIMEFRAME_UNITS_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    """
    把周期字符串(如 "1m" / "1h")换算为秒数, 全项目唯一权威实现
    @param timeframe: 周期字符串, 单位必须小写(s/m/h/d/w)
    @raise ValueError: 非法周期
    """
    unit = timeframe[-1:]
    value = timeframe[:-1]
    if unit not in _TIMEFRAME_UNITS_SECONDS or not value.isdigit():
        raise ValueError(f"无法解析K线周期: {timeframe}")
    return int(value) * _TIMEFRAME_UNITS_SECONDS[unit]


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


class SignalDirection(Enum):
    """行情方向（纯行情事实，与持仓方向无关）"""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"

    def opposite(self) -> "SignalDirection":
        """反向方向；NEUTRAL 的反向仍是 NEUTRAL（中性没有反向可言）"""
        if self is SignalDirection.LONG:
            return SignalDirection.SHORT
        if self is SignalDirection.SHORT:
            return SignalDirection.LONG
        return SignalDirection.NEUTRAL

    @classmethod
    def of_order_side(cls, side: OrderSide) -> "SignalDirection":
        """下单方向 → 行情方向"""
        return cls.LONG if side == OrderSide.BUY else cls.SHORT

    def to_order_side(self) -> OrderSide:
        """行情方向 → 下单方向

        @note NEUTRAL 无下单方向，归为 BUY；调用方须先排除 NEUTRAL
        """
        return OrderSide.SELL if self is SignalDirection.SHORT else OrderSide.BUY


class SignalKind(Enum):
    """信号种类：策略如何解读行情方向

    - `TRIGGER`：无方向倾向，任何非中性信号都触发入场与离场；
    - `DIRECTIONAL`：有方向倾向，同向许可入场、反向许可离场。
    """

    TRIGGER = "trigger"
    DIRECTIONAL = "directional"


class MarketSignal(BaseModel):
    """行情信号快照：只描述"现在是什么状态"，不含任何持仓语义

    不入库（无 signals 表）。策略按 K 线 memo，保证每根 K 线最多求值一次。
    """

    model_config = ConfigDict(extra="forbid")

    direction: SignalDirection = SignalDirection.NEUTRAL
    datetime: str = ""  # 触发该信号的 K 线时间；空串表示尚无触发


class PositionLimit(BaseModel):
    """仓位数量上限：开仓上限闸门与"达到上限全部止损"的唯一权威来源"""

    model_config = ConfigDict(extra="forbid")

    max_order: int
    enable_stop_loss: bool = False

    def reached(self, position_count: int) -> bool:
        """仓位数已达上限，禁止再开仓"""
        return position_count >= self.max_order

    def stop_loss_triggered(self, position_count: int) -> bool:
        """只剩 <=1 个空位时触发全部止损

        @note max_order <= 1 时恒为 False: 容量为 1 时空仓与满仓相邻,
              不存在"最后一格"状态 —— 空仓触发会永不开第一单, 满仓触发
              会开了就强平(开平抖动)。单仓位保护由单笔止损承担。
        """
        if self.max_order <= 1:
            return False
        return self.enable_stop_loss and self.max_order - position_count <= 1
