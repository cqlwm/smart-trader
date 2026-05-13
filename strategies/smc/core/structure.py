import pandas as pd

from strategies.smc.models import Bias, EventType, Pivot, StructureEvent, StructureState


def detect_structure_breaks(
    df: pd.DataFrame,
    pivots_high: list[Pivot],
    pivots_low: list[Pivot],
    initial_trend: Bias = Bias.NEUTRAL,
    swing_pivots_high: list[Pivot] | None = None,
    swing_pivots_low: list[Pivot] | None = None,
    filter_confluence: bool = False,
) -> tuple[list[StructureEvent], StructureState]:
    events: list[StructureEvent] = []
    trend = initial_trend

    high_idx = 0
    low_idx = 0

    current_pivot_high: Pivot | None = None
    current_pivot_low: Pivot | None = None
    high_crossed = False
    low_crossed = False

    swing_high_idx = 0
    swing_low_idx = 0
    current_swing_high: Pivot | None = None
    current_swing_low: Pivot | None = None

    closes = df["close"]
    datetime_strs = df["datetime"].astype(str).tolist()

    for bar in range(len(df)):
        bar_time = datetime_strs[bar]

        while high_idx < len(pivots_high) and pivots_high[high_idx].bar_time <= bar_time:
            current_pivot_high = pivots_high[high_idx]
            high_crossed = False
            high_idx += 1

        while low_idx < len(pivots_low) and pivots_low[low_idx].bar_time <= bar_time:
            current_pivot_low = pivots_low[low_idx]
            low_crossed = False
            low_idx += 1

        if swing_pivots_high is not None:
            while (
                swing_high_idx < len(swing_pivots_high)
                and swing_pivots_high[swing_high_idx].bar_time <= bar_time
            ):
                current_swing_high = swing_pivots_high[swing_high_idx]
                swing_high_idx += 1

        if swing_pivots_low is not None:
            while (
                swing_low_idx < len(swing_pivots_low)
                and swing_pivots_low[swing_low_idx].bar_time <= bar_time
            ):
                current_swing_low = swing_pivots_low[swing_low_idx]
                swing_low_idx += 1

        if bar == 0:
            continue

        close_now = closes.iloc[bar]
        close_prev = closes.iloc[bar - 1]

        if current_pivot_high is not None and not high_crossed:
            level = current_pivot_high.price
            crossover = close_now > level and close_prev <= level

            extra = True
            if filter_confluence and current_swing_high is not None:
                extra = current_pivot_high.price != current_swing_high.price

            if crossover and extra:
                event_type = EventType.CHOCH if trend == Bias.BEARISH else EventType.BOS
                event = StructureEvent(
                    event_type=event_type,
                    bias=Bias.BULLISH,
                    price=close_now,
                    time=str(df["datetime"].iloc[bar]),
                    pivot=current_pivot_high,
                )
                events.append(event)
                high_crossed = True
                trend = Bias.BULLISH

        if current_pivot_low is not None and not low_crossed:
            level = current_pivot_low.price
            crossunder = close_now < level and close_prev >= level

            extra = True
            if filter_confluence and current_swing_low is not None:
                extra = current_pivot_low.price != current_swing_low.price

            if crossunder and extra:
                event_type = EventType.CHOCH if trend == Bias.BULLISH else EventType.BOS
                event = StructureEvent(
                    event_type=event_type,
                    bias=Bias.BEARISH,
                    price=close_now,
                    time=str(df["datetime"].iloc[bar]),
                    pivot=current_pivot_low,
                )
                events.append(event)
                low_crossed = True
                trend = Bias.BEARISH

    last_labels: list[str] = []
    for p in pivots_low[-3:]:
        last_labels.append(p.label)
    for p in pivots_high[-3:]:
        last_labels.append(p.label)

    state = StructureState(
        trend=trend,
        last_event=events[-1] if events else None,
        pivot_high=current_pivot_high,
        pivot_low=current_pivot_low,
        swing_labels=last_labels,
    )
    return events, state
