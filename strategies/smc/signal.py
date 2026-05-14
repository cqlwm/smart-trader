import uuid

from strategies.smc.models.types import OrderBlock, StructureBreak, Bias, OBStatus
from strategies.smc.engine import SMCResult
from strategies.smc.models.signal_types import TradingSignal, TradingSignalState, SignalStatus


def find_entry_ob(
    order_blocks: list[OrderBlock],
    event: StructureBreak,
) -> OrderBlock | None:
    candidates = [
        ob for ob in order_blocks
        if ob.status != OBStatus.MITIGATED and ob.bias == event.bias
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda ob: ob.formed_time, reverse=True)
    return candidates[0]


def find_take_profit_ob(
    order_blocks: list[OrderBlock],
    direction: Bias,
    entry: float,
    stop_loss: float,
    min_rr: float = 2.0,
) -> OrderBlock | None:
    opposite = Bias.BEARISH if direction == Bias.BULLISH else Bias.BULLISH
    candidates = [
        ob for ob in order_blocks
        if ob.status != OBStatus.MITIGATED and ob.bias == opposite
    ]
    candidates.sort(key=lambda ob: ob.formed_time, reverse=True)

    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None

    for ob in candidates:
        tp_price = ob.low if direction == Bias.BULLISH else ob.high
        rr = abs(tp_price - entry) / risk
        if rr > min_rr:
            return ob
    return None


def calculate_stop_loss(
    ob: OrderBlock,
    direction: Bias,
    atr: float,
    atr_multiplier: float = 0.5,
) -> float:
    if direction == Bias.BULLISH:
        return ob.low - atr * atr_multiplier
    return ob.high + atr * atr_multiplier


def _detect_new_event(
    last_event: StructureBreak | None,
    prev_time: str,
) -> StructureBreak | None:
    if last_event is None:
        return None
    if last_event.time > prev_time:
        return last_event
    return None


def _create_signal(
    event: StructureBreak,
    ob: OrderBlock,
    atr: float,
    atr_multiplier: float,
    min_rr: float,
    order_blocks: list[OrderBlock],
    bar_time: str,
) -> TradingSignal | None:
    stop_loss = calculate_stop_loss(ob, event.bias, atr, atr_multiplier)
    entry_price = ob.mid
    tp_ob = find_take_profit_ob(order_blocks, event.bias, entry_price, stop_loss, min_rr)
    take_profit = (ob.low if event.bias == Bias.BULLISH else ob.high) if tp_ob else None
    if tp_ob is None:
        take_profit = None

    return TradingSignal(
        id=uuid.uuid4().hex[:8],
        ob=ob,
        event=event,
        direction=event.bias,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        created_bar_time=bar_time,
        status=SignalStatus.PENDING,
    )


def _update_signal_statuses(
    signals: list[TradingSignal],
    result: SMCResult,
    current_high: float,
    current_low: float,
) -> list[TradingSignal]:
    mitigated_ids: set[str] = set()
    for ob in result.swing_order_blocks:
        if ob.status == OBStatus.MITIGATED:
            mitigated_ids.add(ob.id)
    for ob in result.internal_order_blocks:
        if ob.status == OBStatus.MITIGATED:
            mitigated_ids.add(ob.id)

    updated: list[TradingSignal] = []
    for sig in signals:
        if sig.status == SignalStatus.PENDING:
            if sig.ob.id in mitigated_ids:
                updated.append(sig.model_copy(update={"status": SignalStatus.CANCELED}))
            elif sig.direction == Bias.BULLISH and current_low <= sig.entry_price:
                updated.append(sig.model_copy(update={"status": SignalStatus.FILLED}))
            elif sig.direction == Bias.BEARISH and current_high >= sig.entry_price:
                updated.append(sig.model_copy(update={"status": SignalStatus.FILLED}))
            else:
                updated.append(sig)
        else:
            updated.append(sig)
    return updated


def compute_signals(
    result: SMCResult,
    prev_state: TradingSignalState,
    current_bar_time: str,
    current_high: float,
    current_low: float,
) -> TradingSignalState:
    new_swing_event = _detect_new_event(result.swing_state.last_event, prev_state.last_swing_event_time)
    new_internal_event = _detect_new_event(result.internal_state.last_event, prev_state.last_internal_event_time)

    new_signals: list[TradingSignal] = []

    if new_swing_event is not None:
        ob = find_entry_ob(result.swing_order_blocks, new_swing_event)
        if ob is not None:
            signal = _create_signal(
                event=new_swing_event,
                ob=ob,
                atr=result.current_atr,
                atr_multiplier=0.5,
                min_rr=2.0,
                order_blocks=result.swing_order_blocks,
                bar_time=current_bar_time,
            )
            if signal is not None:
                new_signals.append(signal)

    if new_internal_event is not None:
        ob = find_entry_ob(result.internal_order_blocks, new_internal_event)
        if ob is not None:
            signal = _create_signal(
                event=new_internal_event,
                ob=ob,
                atr=result.current_atr,
                atr_multiplier=0.5,
                min_rr=2.0,
                order_blocks=result.internal_order_blocks,
                bar_time=current_bar_time,
            )
            if signal is not None:
                new_signals.append(signal)

    updated_existing = _update_signal_statuses(
        prev_state.signals, result, current_high, current_low,
    )

    all_signals = updated_existing + new_signals

    new_swing_time = (
        new_swing_event.time
        if new_swing_event
        else prev_state.last_swing_event_time
    )
    new_internal_time = (
        new_internal_event.time
        if new_internal_event
        else prev_state.last_internal_event_time
    )

    return TradingSignalState(
        signals=all_signals,
        last_swing_event_time=new_swing_time,
        last_internal_event_time=new_internal_time,
    )
