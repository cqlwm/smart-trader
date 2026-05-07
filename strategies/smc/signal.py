from __future__ import annotations

from typing import TYPE_CHECKING

from strategies.smc.schemas import (
    Entry,
    ExecutionInfo,
    PositionSizing,
    SignalInfo,
    SignalResult,
    StopLoss,
    Target,
)
from strategies.smc.types import Bias, EventType, FVGStatus, FairValueGap, OrderBlock, ZonePosition

if TYPE_CHECKING:
    from strategies.smc.engine import SMCResult


def compute_signal(result: SMCResult) -> SignalResult:
    current_price = float(result.df["close"].iloc[-1])
    swing_trend = result.swing_state.trend
    internal_state = result.internal_state

    scores = _compute_confluence(result, current_price)
    total = sum(scores.values())
    max_score = len(scores)

    action, confidence = _determine_action(swing_trend, total, max_score)
    reasons = [k for k, v in scores.items() if v == 1]
    invalidation = _compute_invalidation(result, action)

    entry_ob = _find_entry_ob(result, action)
    entry = _compute_entry(entry_ob, current_price)
    stop_loss = _compute_stop_loss(entry_ob, action, result.current_atr)
    targets = _compute_targets(result, action, entry, stop_loss)
    sizing = _compute_position_sizing(
        entry, stop_loss, result.config.account_balance, result.config.risk_per_trade_pct
    )

    return SignalResult(
        signal=SignalInfo(
            action=action,
            confidence=confidence,
            reason=reasons,
            invalidation=invalidation,
        ),
        execution=ExecutionInfo(
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            position_sizing=sizing,
        ),
    )


def _compute_confluence(result: SMCResult, current_price: float) -> dict[str, int]:
    scores: dict[str, int] = {}
    swing_trend = result.swing_state.trend

    scores["swing_trend_bullish"] = 1 if swing_trend == Bias.BULLISH else 0

    internal_event = result.internal_state.last_event
    if internal_event and internal_event.event_type == EventType.CHOCH:
        scores["internal_choch_aligned"] = 1 if internal_event.bias == swing_trend else 0
    else:
        scores["internal_choch_aligned"] = 0

    if swing_trend == Bias.BULLISH:
        scores["price_in_zone"] = 1 if result.premium_discount.current_position == ZonePosition.DISCOUNT else 0
    else:
        scores["price_in_zone"] = 1 if result.premium_discount.current_position == ZonePosition.PREMIUM else 0

    obs = result.swing_order_blocks + result.internal_order_blocks
    matching_obs = [ob for ob in obs if ob.bias == swing_trend]
    scores["ob_available"] = 1 if matching_obs else 0

    scores["fvg_confluence"] = 1 if _has_fvg_ob_overlap(result.fvgs, matching_obs) else 0

    eqh = [eq for eq in result.equal_levels if eq.level_type == "EQH"]
    eql = [eq for eq in result.equal_levels if eq.level_type == "EQL"]
    if swing_trend == Bias.BULLISH:
        scores["liquidity_target"] = 1 if eqh else 0
    else:
        scores["liquidity_target"] = 1 if eql else 0

    return scores


def _has_fvg_ob_overlap(fvgs: list[FairValueGap], obs: list[OrderBlock]) -> bool:
    for fvg in fvgs:
        if fvg.status == FVGStatus.FILLED:
            continue
        for ob in obs:
            if fvg.top >= ob.low and fvg.bottom <= ob.high:
                return True
    return False


def _compute_invalidation(result: SMCResult, action: str) -> list[str]:
    invalidations: list[str] = []
    if action == "LONG":
        obs = [ob for ob in result.swing_order_blocks if ob.bias == Bias.BULLISH]
        for ob in obs:
            invalidations.append(f"close_below_{ob.id}_low_{ob.low}")
        invalidations.append("swing_choch_bearish")
    elif action == "SHORT":
        obs = [ob for ob in result.swing_order_blocks if ob.bias == Bias.BEARISH]
        for ob in obs:
            invalidations.append(f"close_above_{ob.id}_high_{ob.high}")
        invalidations.append("swing_choch_bullish")
    return invalidations


def _determine_action(swing_trend: Bias, total: int, max_score: int) -> tuple[str, float]:
    confidence = total / max_score if max_score > 0 else 0.0
    if total >= 4 and swing_trend == Bias.BULLISH:
        return "LONG", confidence
    if total >= 4 and swing_trend == Bias.BEARISH:
        return "SHORT", confidence
    return "WAIT", confidence


def _find_entry_ob(result: SMCResult, action: str) -> OrderBlock | None:
    if action == "WAIT":
        return None
    bias = Bias.BULLISH if action == "LONG" else Bias.BEARISH
    all_obs = result.swing_order_blocks + result.internal_order_blocks
    matching = [ob for ob in all_obs if ob.bias == bias]
    if not matching:
        return None
    current_price = float(result.df["close"].iloc[-1])
    return min(matching, key=lambda ob: abs(ob.mid - current_price))


def _compute_entry(ob: OrderBlock | None, current_price: float) -> Entry:
    if ob is None:
        return Entry(price=current_price, type="MARKET", zone_high=current_price, zone_low=current_price)
    return Entry(
        price=ob.mid,
        type="LIMIT",
        zone_high=ob.high,
        zone_low=ob.low,
    )


def _compute_stop_loss(ob: OrderBlock | None, action: str, atr: float) -> StopLoss:
    buffer = atr * 0.2
    if ob is None:
        return StopLoss(price=0.0, basis="NONE", distance_pct=0.0)
    if action == "LONG":
        sl_price = ob.low - buffer
        dist_pct = abs(ob.mid - sl_price) / ob.mid * 100 if ob.mid else 0
        return StopLoss(price=sl_price, basis="OB_LOW", distance_pct=dist_pct)
    sl_price = ob.high + buffer
    dist_pct = abs(sl_price - ob.mid) / ob.mid * 100 if ob.mid else 0
    return StopLoss(price=sl_price, basis="OB_HIGH", distance_pct=dist_pct)


def _compute_targets(result: SMCResult, action: str, entry: Entry, stop_loss: StopLoss) -> list[Target]:
    entry_price = entry.price
    sl_price = stop_loss.price
    risk = abs(entry_price - sl_price)
    if risk == 0:
        return []

    targets = []
    pivot = result.internal_state.pivot_high if action == "LONG" else result.internal_state.pivot_low
    if pivot:
        rr = abs(pivot.price - entry_price) / risk
        targets.append(Target(level=1, price=pivot.price, rr=rr, basis="INTERNAL_PIVOT"))

    eqs = result.equal_levels
    if action == "LONG":
        eqh_targets = [eq for eq in eqs if eq.level_type == "EQH" and eq.price > entry_price]
        if eqh_targets:
            eq = min(eqh_targets, key=lambda e: e.price)
            rr = abs(eq.price - entry_price) / risk
            targets.append(Target(level=2, price=eq.price, rr=rr, basis="LIQUIDITY_POOL"))
    else:
        eql_targets = [eq for eq in eqs if eq.level_type == "EQL" and eq.price < entry_price]
        if eql_targets:
            eq = max(eql_targets, key=lambda e: e.price)
            rr = abs(entry_price - eq.price) / risk
            targets.append(Target(level=2, price=eq.price, rr=rr, basis="LIQUIDITY_POOL"))

    if action == "LONG":
        ext_price = entry_price + 3 * risk
    else:
        ext_price = entry_price - 3 * risk
    targets.append(Target(level=3, price=ext_price, rr=3.0, basis="EXTENSION"))

    return targets


def _compute_position_sizing(
    entry: Entry, stop_loss: StopLoss, balance: float, risk_pct: float
) -> PositionSizing:
    risk_amount = balance * risk_pct / 100
    entry_price = entry.price
    sl_price = stop_loss.price
    distance = abs(entry_price - sl_price)

    if distance == 0 or entry_price == 0:
        return PositionSizing(
            account_balance=balance,
            risk_per_trade_pct=risk_pct,
            risk_amount=risk_amount,
            position_value=0.0,
            leverage=0.0,
            recommended_leverage=1.0,
        )

    position_value = risk_amount / (distance / entry_price)
    leverage = position_value / balance

    return PositionSizing(
        account_balance=balance,
        risk_per_trade_pct=risk_pct,
        risk_amount=risk_amount,
        position_value=position_value,
        leverage=leverage,
        recommended_leverage=max(1.0, leverage),
    )
