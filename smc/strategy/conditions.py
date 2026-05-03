from __future__ import annotations

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import TYPE_CHECKING

from smc.mtf import MultiTimeframeResult
from smc.types import Bias, OBStatus

if TYPE_CHECKING:
    from smc.strategy.intraday import SetupZone, TriggerContext


class EntryConditionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    """入场条件检查结果"""

    name: str
    passed: bool
    description: str
    weight: int = 1
    is_hard_filter: bool = False


class EntryConditionChecker:
    """
    入场条件检查器

    硬门槛:
    - setup 位置合理
    - 触发周期存在同向结构确认

    评分项:
    - OB 新鲜度
    - OB 质量
    - FVG 共振
    - 流动性清扫
    - 当前价格位置
    - 大周期方向一致
    """

    def check_all_conditions(
        self,
        mtf_result: MultiTimeframeResult,
        setup_zone: "SetupZone",
        trigger_context: "TriggerContext | None",
        direction: Bias,
        current_price: float,
    ) -> list[EntryConditionResult]:
        results = [
            self._check_setup_zone_position(setup_zone),
            self._check_trigger_structure(trigger_context),
            self._check_ob_freshness(setup_zone),
            self._check_ob_quality(setup_zone),
            self._check_fvg_confluence(setup_zone),
            self._check_liquidity_sweep(trigger_context),
            self._check_price_in_zone(setup_zone, current_price),
            self._check_direction_alignment(mtf_result, direction),
        ]
        return results

    def _check_setup_zone_position(self, setup_zone: "SetupZone") -> EntryConditionResult:
        passed = setup_zone.zone_position_ok
        return EntryConditionResult(
            name="Setup 区域位置",
            passed=passed,
            description="setup 位于合理交易区" if passed else "setup 处于逆势区域",
            weight=3,
            is_hard_filter=True,
        )

    def _check_trigger_structure(self, trigger_context: "TriggerContext | None") -> EntryConditionResult:
        passed = bool(trigger_context and trigger_context.structure_aligned)
        description = (
            f"{trigger_context.timeframe} 已出现同向结构触发"
            if passed and trigger_context
            else "触发周期尚未出现同向 CHoCH/BOS"
        )
        return EntryConditionResult(
            name="触发结构确认",
            passed=passed,
            description=description,
            weight=4,
            is_hard_filter=True,
        )

    def _check_ob_freshness(self, setup_zone: "SetupZone") -> EntryConditionResult:
        status = setup_zone.order_block.status
        passed = status in (OBStatus.UNTESTED, OBStatus.TESTED)
        if status == OBStatus.UNTESTED:
            description = "订单块未被测试过"
        elif status == OBStatus.TESTED:
            description = "订单块已测试但未失效"
        else:
            description = "订单块已失效"
        return EntryConditionResult(
            name="订单块新鲜度",
            passed=passed,
            description=description,
            weight=3,
        )

    def _check_ob_quality(self, setup_zone: "SetupZone") -> EntryConditionResult:
        ob = setup_zone.order_block
        ob_range = abs(ob.high - ob.low)
        mid_price = (ob.high + ob.low) / 2
        range_pct = ob_range / mid_price * 100 if mid_price else 0
        passed = 0.1 < range_pct < 5.0
        return EntryConditionResult(
            name="订单块质量",
            passed=passed,
            description=f"OB 范围 {range_pct:.2f}%",
            weight=2,
        )

    def _check_fvg_confluence(self, setup_zone: "SetupZone") -> EntryConditionResult:
        passed = setup_zone.overlapping_fvg is not None
        return EntryConditionResult(
            name="FVG 共振",
            passed=passed,
            description="OB 与同向 FVG 重叠" if passed else "缺少 FVG 共振",
            weight=3,
        )

    def _check_liquidity_sweep(self, trigger_context: "TriggerContext | None") -> EntryConditionResult:
        passed = bool(trigger_context and trigger_context.liquidity_swept)
        return EntryConditionResult(
            name="流动性清扫",
            passed=passed,
            description="触发层完成流动性清扫" if passed else "未观察到明确流动性清扫",
            weight=2,
        )

    def _check_price_in_zone(self, setup_zone: "SetupZone", current_price: float) -> EntryConditionResult:
        ob = setup_zone.order_block
        passed = ob.low <= current_price <= ob.high
        return EntryConditionResult(
            name="价格位置",
            passed=passed,
            description="当前价格在 setup 区间内" if passed else "当前价格尚未回到 setup 区间",
            weight=2,
        )

    def _check_direction_alignment(
        self,
        mtf_result: MultiTimeframeResult,
        direction: Bias,
    ) -> EntryConditionResult:
        if "1d" not in mtf_result.results:
            return EntryConditionResult(
                name="大周期方向对齐",
                passed=False,
                description="无法获取 1d 周期数据",
                weight=3,
            )

        daily_state = mtf_result.results["1d"].swing_state
        passed = daily_state.trend == direction
        return EntryConditionResult(
            name="大周期方向对齐",
            passed=passed,
            description="与 1d 摆动趋势一致" if passed else "与 1d 摆动趋势相反",
            weight=3,
        )
