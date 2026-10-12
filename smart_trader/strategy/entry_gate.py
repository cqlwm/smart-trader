"""入场闸门链: 可组合的开仓条件判定

每个闸门只负责一个条件, 互不知晓; `EntryGateChain` 短路求值。
闸门不得 import 任何策略模块, 也不得构造仓位或下单。
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import MarketSignal, OrderSide, PositionLimit, SignalDirection
from smart_trader.strategy.grid_position import GridPosition


class EntryContext(BaseModel):
    """入场闸门上下文: 纯数据, 不含 Kline / DataFrame

    @param positions 当前逻辑仓位快照
    @param close 最新收盘价; None 表示尚无行情
    @param market_signal 本根 K 线的行情信号; None 表示未配置信号（放行）
    """

    model_config = ConfigDict(extra="forbid")

    positions: list[GridPosition] = []
    close: float | None = None
    market_signal: MarketSignal | None = None

    def require_close(self) -> float:
        """取最新收盘价

        @raise SmartTraderError 尚无行情时抛出
        @note 不用 assert: python -O 下 assert 会被剥离
        """
        if self.close is None:
            raise SmartTraderError(
                ErrorCode.MARKET_DATA_UNAVAILABLE, "尚无行情, 无法取最新收盘价"
            )
        return self.close


class EntryGate(ABC):
    """入场闸门: 只判断一个条件是否满足"""

    @abstractmethod
    def allow(self, ctx: EntryContext) -> bool:
        """@return True 表示允许开仓"""


class EntryGateChain:
    """闸门链: 短路 AND, 空链恒放行"""

    def __init__(self, gates: Sequence[EntryGate]) -> None:
        self._gates = list(gates)

    @property
    def gates(self) -> list[EntryGate]:
        """已装配的闸门（按声明顺序, 只读）"""
        return list(self._gates)

    def allows(self, ctx: EntryContext) -> bool:
        for gate in self._gates:
            if not gate.allow(ctx):
                return False
        return True


class MaxPositionsGate(EntryGate):
    """仓位数量未达上限"""

    def __init__(self, limit: PositionLimit) -> None:
        self._limit = limit

    def allow(self, ctx: EntryContext) -> bool:
        return not self._limit.reached(len(ctx.positions))


class SignalTriggerGate(EntryGate):
    """无方向倾向: 任何非中性信号都触发入场

    替代旧 `AlphaTrendGridsSignal`（其 is_entry 与 is_exit 返回同一条件）。
    """

    def allow(self, ctx: EntryContext) -> bool:
        signal = ctx.market_signal
        return signal is None or signal.direction is not SignalDirection.NEUTRAL


class DirectionalSignalGate(EntryGate):
    """有方向倾向: 仅当信号方向与入场方向一致时放行

    @param entry_side 策略的实际入场方向（多头为 BUY）
    """

    def __init__(self, entry_side: OrderSide) -> None:
        self._entry_side = entry_side

    @property
    def entry_side(self) -> OrderSide:
        """期望的入场方向（只读）"""
        return self._entry_side

    def allow(self, ctx: EntryContext) -> bool:
        signal = ctx.market_signal
        if signal is None:
            return True
        return signal.direction is SignalDirection.of_order_side(self._entry_side)


class PriceRangeGate(EntryGate):
    """收盘价落在可交易区间内（闭区间, 含等号）"""

    def __init__(self, lowest_price: float, highest_price: float) -> None:
        self._lowest = lowest_price
        self._highest = highest_price

    def allow(self, ctx: EntryContext) -> bool:
        close = ctx.close
        if close is None:
            return False
        return self._lowest <= close <= self._highest


class GridSpacingGate(EntryGate):
    """网格间距: 相对基准仓位的距离限制

    判定 `profit_and_loss_ratio(close) <= -spacing_rate`（含等号）。
    **间距的正负号决定语义**:

    - 正值: `ratio <= -rate`, **单向** —— 仅亏损方向走出间距后加仓, 盈利方向拦截;
    - 负值: `ratio <= |rate|`, **双向距离限制** —— 距基准价不超过阈值即加仓,
      顺势下跌与逆势上涨小幅都会放行。

    无仓位时直接放行（第一单不受间距约束）。

    @param spacing_rate 间距阈值; 正数为单向间距, 负数为双向距离限制
    @param master_side 网格主攻方向, 决定取最低价还是最高价仓位为基准
    """

    def __init__(self, spacing_rate: float, master_side: OrderSide) -> None:
        self._spacing_rate = spacing_rate
        self._master_side = master_side

    def allow(self, ctx: EntryContext) -> bool:
        if not ctx.positions:
            return True
        if ctx.close is None:
            return False
        benchmark = self._master_side.extremum_fun()(
            ctx.positions, key=lambda position: position.price
        )
        return benchmark.profit_and_loss_ratio(ctx.close) <= -self._spacing_rate


class TrendSpacingGate(EntryGate):
    """顺势间距: 顺盈利方向走出间距才加仓（顺势阶梯）

    判定 `profit_and_loss_ratio(close) >= spacing_rate`（含等号），基准取
    **最外层仓位**（盈利方向极值: 多头取最高价层、空头取最低价层）。
    无仓位时直接放行（第一单不受间距约束）。

    与 `GridSpacingGate` 方向相反（后者以亏损方向极值为基准, 只放行逆势补仓）,
    二者语义互斥, 由配置二选一装配。

    @param spacing_rate 顺势间距阈值
    @param master_side 网格主攻方向, 决定取哪一端为最外层基准
    """

    def __init__(self, spacing_rate: float, master_side: OrderSide) -> None:
        self._spacing_rate = spacing_rate
        self._master_side = master_side

    def allow(self, ctx: EntryContext) -> bool:
        if not ctx.positions:
            return True
        if ctx.close is None:
            return False
        benchmark = self._master_side.reversal().extremum_fun()(
            ctx.positions, key=lambda position: position.price
        )
        return benchmark.profit_and_loss_ratio(ctx.close) >= self._spacing_rate


class MaxOrderStopLossGate(EntryGate):
    """达到仓位上限已触发全部止损时, 不再开新仓

    与离场的强制平仓读同一个 `PositionLimit`, 杜绝两处判定漂移。
    """

    def __init__(self, limit: PositionLimit) -> None:
        self._limit = limit

    def allow(self, ctx: EntryContext) -> bool:
        return not self._limit.stop_loss_triggered(len(ctx.positions))