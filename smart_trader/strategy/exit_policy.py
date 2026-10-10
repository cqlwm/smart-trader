"""离场策略链: 可组合的离场判定

每个策略只负责一个离场条件, 互不知晓; `ExitPolicyChain` 把决策合并。
策略不得下单、撤单或归档 —— 那是 `SignalGridStrategy` 的职责。
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from smart_trader.model import MarketSignal, OrderSide, SignalDirection, SignalKind
from smart_trader.strategy.grid_position import GridPosition


class ExitContext(BaseModel):
    """离场策略上下文: 纯数据, 不含 Kline / DataFrame

    @param close 最新收盘价
    @param market_signal 本根 K 线的行情信号; None 表示未配置信号
    """

    model_config = ConfigDict(extra="forbid")

    close: float = 0.0
    market_signal: MarketSignal | None = None


class ExitDecision(BaseModel):
    """离场决策

    @param should_exit 是否应当离场
    @param force 是否强制离场（可覆盖休息中的出场挂单: 撤单结算后重挂）

    @note 止盈类策略恒为 force=False, 保留原挂单不重复下单
    """

    model_config = ConfigDict(extra="forbid")

    should_exit: bool = False
    force: bool = False


class ExitPolicy(ABC):
    """离场策略: 只判断一个条件"""

    @abstractmethod
    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision: ...


class ExitPolicyChain:
    """离场策略链: 合并全部策略的决策

    合并规则 `should_exit = any` / `force = any`, **与策略顺序无关**。
    """

    def __init__(self, policies: Sequence[ExitPolicy]) -> None:
        self._policies = list(policies)

    @property
    def policies(self) -> list[ExitPolicy]:
        """已装配的离场策略（只读）"""
        return list(self._policies)

    def decide(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        should_exit = False
        force = False
        for policy in self._policies:
            decision = policy.evaluate(position, ctx)
            should_exit = should_exit or decision.should_exit
            force = force or decision.force
        return ExitDecision(should_exit=should_exit, force=force)


class ForceExitPolicy(ExitPolicy):
    """仓位外部强制离场（达到仓位上限止损 / 手动 close_position）

    @param trigger 回调: 返回 True 时全部仓位强制离场
    """

    def __init__(self, trigger: Callable[[], bool]) -> None:
        self._trigger = trigger

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        if self._trigger():
            return ExitDecision(should_exit=True, force=True)
        return ExitDecision()


class StopLossPolicy(ExitPolicy):
    """仓位自身止损

    判定用仓位的 `trailing_stop_price`（固定止损与跟踪止损共用同一字段）,
    方向由仓位的 `entry_side` 决定, 含等号。
    """

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        if not position.enable_stop_loss or position.trailing_stop_price is None:
            return ExitDecision()

        stop_price = position.trailing_stop_price
        if position.entry_side == OrderSide.BUY:
            triggered = ctx.close <= stop_price
        else:
            triggered = ctx.close >= stop_price
        if not triggered:
            return ExitDecision()
        return ExitDecision(should_exit=True, force=True)


class FixedTakeProfitPolicy(ExitPolicy):
    """固定比例止盈（`profit_level` 档位 2）

    是否启用由"是否放进策略链"表达, 不设开关字段。
    阈值含等号与否由 `GridPosition.profit_level` 决定（严格 `>`, 不含等号）。
    """

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        if position.profit_level(ctx.close) == 2:
            return ExitDecision(should_exit=True)
        return ExitDecision()


class SignalExitPolicy(ExitPolicy):
    """信号离场（`profit_level` 档位 1 且行情方向命中）

    止盈阈值直接用 `position.signal_min_take_profit_rate`（`profit_level` 已在用）,
    不持有第二份参数, 避免参数源分裂。

    @param kind TRIGGER: 任何非中性信号即可; DIRECTIONAL: 仅命中出场方向时
    @param exit_side 策略的实际出场方向（多头仓位的出场方向为 SELL）
    @param enabled 是否启用信号离场
    """

    def __init__(
        self,
        kind: SignalKind,
        exit_side: OrderSide,
        enabled: bool = True,
    ) -> None:
        self._kind = kind
        self._exit_side = exit_side
        self._enabled = enabled

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        if not self._enabled:
            return ExitDecision()

        signal = ctx.market_signal
        if signal is None or signal.direction is SignalDirection.NEUTRAL:
            return ExitDecision()

        if (
            self._kind is SignalKind.DIRECTIONAL
            and signal.direction is not SignalDirection.of_order_side(self._exit_side)
        ):
            return ExitDecision()

        if position.profit_level(ctx.close) == 1:
            return ExitDecision(should_exit=True)
        return ExitDecision()


class RestingTakeProfitPolicy:
    """盘中休息限价止盈单挂单价

    **不进 `ExitPolicyChain`**: 它不判断是否离场, 只提供挂单价。
    止盈率取自仓位的 `fixed_take_profit_rate`（与 `profit_level` 同源）。

    @param entry_side 策略的实际入场方向（多头为 BUY）
    """

    def __init__(self, entry_side: OrderSide) -> None:
        self._entry_side = entry_side

    def limit_price(self, position: GridPosition) -> float:
        return position.price * (
            1 + self._entry_side.to_int() * position.fixed_take_profit_rate
        )