"""持仓对账: 每根完成K线以交易所实际持仓为准收敛本地缺口

背景: 用户可能在交易所手动平掉部分仓位, 本地账本(纯订单派生)不知道,
策略按本地数量挂出的平仓单会被交易所整单拒绝(-2022), 每根K线陷入
"拒单 → 吞异常 → 重试"死循环, 仓位永远平不掉。

对账器每根完成K线经 `AccountPort.positions` 查询交易所实际持仓, 与本地
派生总量比对:

- **本地多出(外部被平)**: 缺口按 LIFO(最外层优先, 盈利方向极值层先分摊)
  分摊 —— 被全吃的仓位先撤挂单结算事实再内部归档(绝不下
  对冲单); 部分被吃的仓位记录外部扣减量, 平仓下单量按 `quantity - 扣减量`
  clamp;
- **交易所多于本地**(手动加仓 / 来源不明仓位): 仅告警, 不自动收编;
- **数量一致**: 无动作。

对账**不回写账本数字**: `orders` 行只保存订单事实, 外部平仓的成交价本地
拿不到, 不伪装记账; 归档仓位保留原始订单行, 由日志留痕。
"""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.exceptions import SmartTraderError
from smart_trader.exchange.ports import AccountPort
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.position_lifecycle import PositionLifecycle
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig

logger = log.getLogger(__name__)

# 数量比对浮点容差: 数量经 step_size 格式化(最小步长远大于 1e-6),
# 该容差只吸收浮点噪声, 不会吞掉真实的外部平仓量
_QTY_EPS = 1e-6


class PositionReconcileResult(BaseModel):
    """一轮持仓对账的结果

    @param archived 交易所已无对应持仓、需策略归档的仓位(挂单已撤、事实已结算)
    @param adjustments 仓位id -> 被外部平掉的量(0 < 扣减 < quantity);
        平仓/休息止盈下单量应 clamp 为 `quantity - 扣减量`
    """

    model_config = ConfigDict(extra="forbid")

    archived: list[GridPosition] = []
    adjustments: dict[str, float] = {}


class PositionReconciler:
    """持仓对账器: 交易所实际持仓 ↔ 本地派生总量"""

    def __init__(
        self,
        config: SignalGridStrategyConfig,
        account: AccountPort,
        positions: Callable[[], list[GridPosition]],
        lifecycle: PositionLifecycle,
    ):
        self._config = config
        self._account = account
        self._positions = positions
        self._lifecycle = lifecycle
        # 上一次已告警的差额: 告警只在差额变化时重复（首轮 None = 必告警）
        self._last_reported_gap: float | None = None

    def reconcile(self) -> PositionReconcileResult:
        """执行一轮持仓对账; 查询异常跳过本轮, 绝不中断主循环"""
        held = [item for item in self._positions() if item.quantity > _QTY_EPS]
        if not held:
            return PositionReconcileResult()

        try:
            exchange_qty = self._exchange_quantity()
        except SmartTraderError as e:
            logger.warning("持仓对账查询失败, 跳过本轮: %s", e)
            return PositionReconcileResult()

        local_qty = sum(item.quantity for item in held)
        gap = local_qty - exchange_qty
        if gap <= _QTY_EPS:
            if gap < -_QTY_EPS:
                if self._mark_gap_reported(gap):
                    logger.warning(
                        "持仓对账告警: 交易所实际持仓(%s)多于本地(%s), 差额来源不明, 不自动改动",
                        exchange_qty,
                        local_qty,
                    )
            else:
                self._log_gap_recovered()
            return PositionReconcileResult()

        warn = self._mark_gap_reported(gap)
        if warn:
            logger.warning(
                "持仓对账: 本地(%s)多于交易所(%s), 缺口 %s 按最外层优先分摊",
                local_qty,
                exchange_qty,
                gap,
            )
        return self._absorb(held, gap, warn)

    def _mark_gap_reported(self, gap: float) -> bool:
        """记录本轮差额并返回是否相对上次告警值发生变化（首次告警视为变化）"""
        last = self._last_reported_gap
        self._last_reported_gap = gap
        return last is None or abs(gap - last) > _QTY_EPS

    def _log_gap_recovered(self) -> None:
        """差额归零时给一条收敛提示; 启动即一致则静默"""
        last = self._last_reported_gap
        self._last_reported_gap = 0.0
        if last is not None and abs(last) > _QTY_EPS:
            logger.info("持仓对账: 差额已收敛(上轮告警差额 %s)", last)

    def _exchange_quantity(self) -> float:
        """交易所该 symbol + position_side 方向的实际持仓量(其余方向不计)"""
        total = 0.0
        for item in self._account.positions(self._config.symbol):
            if item.side is self._config.position_side:
                total += item.quantity
        return total

    def _absorb(
        self, held: list[GridPosition], gap: float, warn: bool
    ) -> PositionReconcileResult:
        """按最外层优先把缺口分摊到仓位: 全吃归档, 部分吃记扣减

        @param warn 差额相对上轮是否变化; 不变时分摊明细日志静默（结果照常产出）
        """
        result = PositionReconcileResult()
        remaining = gap
        for position in self._outermost_first(held):
            if remaining <= _QTY_EPS:
                break
            cut = min(remaining, position.quantity)
            remaining -= cut
            if cut >= position.quantity - _QTY_EPS:
                if self._close_internally(position):
                    result.archived.append(position)
            else:
                result.adjustments[position.id] = cut
                if warn:
                    logger.warning(
                        "仓位 %s 被外部平掉 %s, 剩余可平量 %s",
                        position.id,
                        cut,
                        position.quantity - cut,
                    )
        return result

    @staticmethod
    def _outermost_first(held: list[GridPosition]) -> list[GridPosition]:
        """最外层优先排序: 盈利方向极值层（多头最高价、空头最低价）优先

        最外层 = 盈利方向极值层（`exit_side.extremum_fun()`）: 多头(exit=SELL,
        max)为最高价层, 空头(exit=BUY, min)为最低价层。
        """
        return sorted(held, key=lambda item: item.price * item.exit_side.to_int())

    def _close_internally(self, position: GridPosition) -> bool:
        """内部关闭: 撤挂单结算事实; 撤单失败保留仓位下一轮重试"""
        try:
            if position.has_pending_exit:
                self._lifecycle.force_settle_exit(position)
            if position.pending_entry_orders:
                self._lifecycle.settle_pending_entries(position)
        except SmartTraderError:
            logger.warning(
                "内部关闭仓位 %s 时撤单失败, 保留待下一轮重试", position.id
            )
            return False
        logger.warning(
            "内部关闭仓位 %s: 交易所已无对应持仓(外部平仓), 不下对冲单",
            position.id,
        )
        return True
