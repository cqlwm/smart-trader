from __future__ import annotations

from typing import TYPE_CHECKING

from strategies.smc.schemas import (
    ContextOutput,
    EqualLevelOutput,
    EventOutput,
    FVGOutput,
    FullOutput,
    LiquidityPool,
    OBOutput,
    OrderBlocksOutput,
    PivotOutput,
    PremiumDiscountOutput,
    StructureOutput,
    StructureOutputState,
    SummaryOutput,
    SwingLabelsOutput,
    ZonesOutput,
)
from strategies.smc.types import Bias, FVGStatus, ZonePosition

if TYPE_CHECKING:
    from strategies.smc.engine import SMCResult
    from strategies.smc.schemas import SignalResult


def build_output(result: SMCResult, signal: SignalResult) -> FullOutput:
    df = result.df
    current_price = float(df["close"].iloc[-1])
    current_time = str(df["datetime"].iloc[-1])

    return FullOutput(
        structure=_build_structure(result),
        zones=_build_zones(result, current_price),
        signal=signal.signal,
        execution=signal.execution,
        context=_build_context(result, current_price, current_time),
        summary=_build_summary(result, signal, current_price),
    )


def _build_structure(result: SMCResult) -> StructureOutput:
    return StructureOutput(
        swing=_structure_state_dict(result.swing_state),
        internal=_structure_state_dict(result.internal_state),
    )


def _structure_state_dict(state) -> StructureOutputState:
    event_dict = None
    if state.last_event:
        event_dict = EventOutput(
            type=state.last_event.event_type.name,
            bias=state.last_event.bias.name,
            price=state.last_event.price,
            time=state.last_event.time,
        )

    pivot_high_dict = None
    if state.pivot_high:
        pivot_high_dict = PivotOutput(
            price=state.pivot_high.price,
            time=state.pivot_high.bar_time,
            label=state.pivot_high.label,
        )

    pivot_low_dict = None
    if state.pivot_low:
        pivot_low_dict = PivotOutput(
            price=state.pivot_low.price,
            time=state.pivot_low.bar_time,
            label=state.pivot_low.label,
        )

    return StructureOutputState(
        trend=state.trend.name,
        last_event=event_dict,
        pivot_high=pivot_high_dict,
        pivot_low=pivot_low_dict,
    )


def _build_zones(result: SMCResult, current_price: float) -> ZonesOutput:
    swing_obs = [_ob_dict(ob, current_price) for ob in result.swing_order_blocks]
    internal_obs = [_ob_dict(ob, current_price) for ob in result.internal_order_blocks]

    active_fvgs = [fvg for fvg in result.fvgs if fvg.status != FVGStatus.FILLED]
    fvg_list = [_fvg_dict(fvg) for fvg in active_fvgs]

    eq_list = [
        EqualLevelOutput(
            type=eq.level_type,
            price=eq.price,
            time=eq.time,
            touches=eq.touches,
        )
        for eq in result.equal_levels
    ]

    pd_zone = result.premium_discount
    pd_dict = PremiumDiscountOutput(
        premium_zone_high=pd_zone.premium_zone_high,
        premium_zone_low=pd_zone.premium_zone_low,
        equilibrium=pd_zone.equilibrium,
        discount_zone_high=pd_zone.discount_zone_high,
        discount_zone_low=pd_zone.discount_zone_low,
        current_position=pd_zone.current_position.name,
    )

    liquidity_pools = _build_liquidity_pools(result)

    return ZonesOutput(
        order_blocks=OrderBlocksOutput(swing=swing_obs, internal=internal_obs),
        fair_value_gaps=fvg_list,
        equal_highs_lows=eq_list,
        premium_discount=pd_dict,
        liquidity_pools=liquidity_pools,
    )


def _ob_dict(ob, current_price: float) -> OBOutput:
    distance_pct = (current_price - ob.mid) / ob.mid * 100 if ob.mid else 0
    return OBOutput(
        id=ob.id,
        bias=ob.bias.name,
        high=ob.high,
        low=ob.low,
        mid=ob.mid,
        formed_time=ob.formed_time,
        status=ob.status.name,
        distance_pct=distance_pct,
    )


def _fvg_dict(fvg) -> FVGOutput:
    return FVGOutput(
        id=fvg.id,
        bias=fvg.bias.name,
        high=fvg.top,
        low=fvg.bottom,
        mid=fvg.mid,
        formed_time=fvg.formed_time,
        status=fvg.status.name,
        fill_pct=fvg.fill_pct,
        width=fvg.width,
        width_atr_ratio=fvg.width_atr_ratio,
        mitigation_depth=fvg.mitigation_depth,
        touch_count=fvg.touch_count,
    )


def _build_liquidity_pools(result: SMCResult) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []

    eqh_prices: dict[float, int] = {}
    eql_prices: dict[float, int] = {}

    for eq in result.equal_levels:
        if eq.level_type == "EQH":
            eqh_prices[eq.price] = eqh_prices.get(eq.price, 0) + eq.touches
        else:
            eql_prices[eq.price] = eql_prices.get(eq.price, 0) + eq.touches

    for price, touches in eqh_prices.items():
        strength = "HIGH" if touches >= 3 else "MEDIUM" if touches >= 2 else "LOW"
        pools.append(LiquidityPool(type="BUY_SIDE", price=price, strength=strength))

    for price, touches in eql_prices.items():
        strength = "HIGH" if touches >= 3 else "MEDIUM" if touches >= 2 else "LOW"
        pools.append(LiquidityPool(type="SELL_SIDE", price=price, strength=strength))

    if result.swing_state.pivot_high:
        ph_price = result.swing_state.pivot_high.price
        if not any(p.price == ph_price and p.type == "BUY_SIDE" for p in pools):
            pools.append(LiquidityPool(
                type="BUY_SIDE",
                price=ph_price,
                strength="MEDIUM",
            ))
    if result.swing_state.pivot_low:
        pl_price = result.swing_state.pivot_low.price
        if not any(p.price == pl_price and p.type == "SELL_SIDE" for p in pools):
            pools.append(LiquidityPool(
                type="SELL_SIDE",
                price=pl_price,
                strength="MEDIUM",
            ))

    return pools


def _build_context(result: SMCResult, current_price: float, current_time: str) -> ContextOutput:
    labels = result.swing_state.swing_labels
    last_label = labels[-1] if labels else ""

    return ContextOutput(
        symbol=result.config.symbol,
        timeframe=result.config.timeframe,
        timestamp=current_time,
        current_price=current_price,
        atr=result.current_atr,
        volatility=result.volatility,
        swing_labels=SwingLabelsOutput(
            last_pivot_type=last_label,
            sequence=labels,
        ),
    )


def _build_summary(result: SMCResult, signal: SignalResult, current_price: float) -> SummaryOutput:
    sig = signal.signal
    swing = result.swing_state
    internal = result.internal_state
    pd_zone = result.premium_discount

    trend_cn = "看多" if swing.trend == Bias.BULLISH else "看空"
    internal_trend_cn = "看多" if internal.trend == Bias.BULLISH else "看空"

    event_desc = ""
    if swing.last_event:
        event_desc = f"最近 {swing.last_event.event_type.name}"

    zone_cn = {
        ZonePosition.PREMIUM: "溢价区",
        ZonePosition.EQUILIBRIUM: "均衡区",
        ZonePosition.DISCOUNT: "折价区",
    }.get(pd_zone.current_position, "")

    narrative = (
        f"{result.config.symbol} {result.config.timeframe} "
        f"摆动结构{trend_cn}（{event_desc}），"
        f"内部结构{internal_trend_cn}。"
        f"当前价格 {current_price} 位于{zone_cn}。"
    )

    ob_count = len(result.swing_order_blocks) + len(result.internal_order_blocks)
    fvg_count = sum(1 for fvg in result.fvgs if fvg.status != FVGStatus.FILLED)
    if ob_count > 0:
        narrative += f"存在 {ob_count} 个活跃订单块。"
    if fvg_count > 0:
        narrative += f"存在 {fvg_count} 个未填补 FVG。"

    confluence = {r: 1 for r in sig.reason}
    total = len(sig.reason)
    confluence["total"] = f"{total}/6"

    targets = signal.execution.targets
    if targets:
        min_rr = min(t.rr for t in targets if t.rr > 0)
        risk_note = f"最小盈亏比 1:{min_rr}。"
    else:
        risk_note = "当前无明确目标。"

    return SummaryOutput(
        market_narrative=narrative,
        confluence_score=confluence,
        risk_note=risk_note,
    )
