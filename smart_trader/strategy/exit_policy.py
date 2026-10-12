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

    @param positions 当前逻辑仓位快照（逐层减仓需要跨仓位选择, 与 EntryContext 对称）
    @param close 最新收盘价
    @param market_signal 本根 K 线的行情信号; None 表示未配置信号
    """

    model_config = ConfigDict(extra="forbid")

    positions: list[GridPosition] = []
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

    `enable_stop_loss` 是总开关, `enable_trailing_stop` 只选模式:
    - 固定止损: 触发价 = 仓位均价 × (1 ∓ stop_loss_rate) 实时计算 —— 活参数,
      修改费率（改库或改配置）后重启即生效;
    - 跟踪止损: 读 ratchet 字段 `trailing_stop_price`（开仓播种、只向盈利方向
      抬升; 缺失时无法判定, 不触发, 也不回退费率计算）。
    方向由仓位的 `entry_side` 决定, 含等号。
    """

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        if not position.enable_stop_loss:
            return ExitDecision()
        stop_price = (
            position.trailing_stop_price
            if position.enable_trailing_stop
            else self._fixed_stop_price(position)
        )
        if stop_price is None:
            return ExitDecision()

        if position.entry_side == OrderSide.BUY:
            triggered = ctx.close <= stop_price
        else:
            triggered = ctx.close >= stop_price
        if not triggered:
            return ExitDecision()
        return ExitDecision(should_exit=True, force=True)

    @staticmethod
    def _fixed_stop_price(position: GridPosition) -> float | None:
        """固定止损触发价: 仓位均价 × (1 ∓ stop_loss_rate); 无成本基准时无法判定"""
        if position.price <= 0:
            return None
        return position.price * (
            1 - position.stop_loss_rate * position.entry_side.to_int()
        )


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
    """信号止盈（反向信号命中且该层**浮盈**）

    止盈的字面语义: 有浮盈（`profit_and_loss_ratio(close) > 0`，即 close 在
    成本价盈利侧）才离场，无盈利门槛；浮亏/持平不归本策略管。
    是否启用由"是否放进策略链"表达（`enable_signal_take_profit` 由装配处决定）,
    不设开关字段。

    @param kind TRIGGER: 任何非中性信号即可; DIRECTIONAL: 仅命中出场方向时
    @param exit_side 策略的实际出场方向（多头仓位的出场方向为 SELL）
    """

    def __init__(self, kind: SignalKind, exit_side: OrderSide) -> None:
        self._kind = kind
        self._exit_side = exit_side

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        signal = ctx.market_signal
        if signal is None or signal.direction is SignalDirection.NEUTRAL:
            return ExitDecision()

        if (
            self._kind is SignalKind.DIRECTIONAL
            and signal.direction is not SignalDirection.of_order_side(self._exit_side)
        ):
            return ExitDecision()

        if position.profit_and_loss_ratio(ctx.close) > 0:
            return ExitDecision(should_exit=True)
        return ExitDecision()


class LayeredReducePolicy(ExitPolicy):
    """信号止损（反向信号命中、该层**浮亏**、且是**最外层**）

    - 只对最外层（盈利方向极值且 `quantity > 0` 的持仓层）产出离场决策,
      其余层不动 —— 策略逐仓位询问策略链, 只有一个返回 True;
    - **要求浮亏**（`profit_and_loss_ratio(close) < 0`）: 止损的字面语义 ——
      浮盈的最外层归止盈分支（`SignalExitPolicy` / 固定止盈）, 不被 force 剥掉;
    - 已有未成交出场单的层仍被选中（`force=True` 撤单重挂, 不越过它去剥下一层）;
    - `force=True`: 逆势减仓必须实际减仓, 每根完成 K 线撤单重挂到最新收盘价。

    由 `enable_signal_stop_loss` 独立开关装配, 与 `SignalExitPolicy` 按盈亏状态
    天然互斥: 浮盈走止盈, 浮亏走止损（Step 24）。

    @param kind TRIGGER: 任何非中性信号即可; DIRECTIONAL: 仅命中出场方向时
    @param exit_side 策略的实际出场方向（多头仓位的出场方向为 SELL）
    """

    def __init__(self, kind: SignalKind, exit_side: OrderSide) -> None:
        self._kind = kind
        self._exit_side = exit_side

    def evaluate(self, position: GridPosition, ctx: ExitContext) -> ExitDecision:
        signal = ctx.market_signal
        if signal is None or signal.direction is SignalDirection.NEUTRAL:
            return ExitDecision()

        if (
            self._kind is SignalKind.DIRECTIONAL
            and signal.direction is not SignalDirection.of_order_side(self._exit_side)
        ):
            return ExitDecision()

        if position.profit_and_loss_ratio(ctx.close) >= 0:
            return ExitDecision()  # 浮盈/持平: 不归止损分支

        outermost = self._outermost(ctx.positions)
        if outermost is None or outermost.id != position.id:
            return ExitDecision()
        return ExitDecision(should_exit=True, force=True)

    def _outermost(self, positions: list[GridPosition]) -> GridPosition | None:
        """最外层: 盈利方向极值且 `quantity > 0` 的持仓层; 无持仓层时为 None"""
        holding = [item for item in positions if item.quantity > 0]
        if not holding:
            return None
        return self._exit_side.extremum_fun()(holding, key=lambda item: item.price)


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