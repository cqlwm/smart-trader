import pandas as pd
from pydantic import BaseModel

from strategies.smc.models import Bias, EventType, Pivot, StructureBreak, StructureState

class StructureBreakResult(BaseModel):
    structure_breaks: list[StructureBreak]
    undestroyed_pivots: list[Pivot]

def detect_structure_breaks_v2(df: pd.DataFrame, pivots: list[Pivot], initial_trend: Bias = Bias.NEUTRAL) -> StructureBreakResult:
    sbs: list[StructureBreak] = []
    filled_pivot_times: list[str] = []
    trend = initial_trend

    closes = df["close"]
    low_prices = df["low"]
    high_prices = df["high"]
    datetime_strs = df["datetime"].astype(str).tolist()

    for bar_i in range(1, len(df)):
        bar_time = datetime_strs[bar_i]
        close_now = closes.iloc[bar_i]
        close_prev = closes.iloc[bar_i - 1]

        for pivot in pivots:
            if pivot.bar_time <= bar_time and pivot.bar_time not in filled_pivot_times:
                pivot_idx = datetime_strs.index(pivot.bar_time)
                range_slice = slice(pivot_idx, bar_i + 1)

                if pivot.is_high:
                    cross = close_now > pivot.price >= close_prev
                    event_type = EventType.CHOCH if trend == Bias.BEARISH else EventType.BOS
                    _bias = Bias.BULLISH
                    # 寻找[pivot.bar_time, bar_time]区间最低价格的K线用来构建OrderBlock
                else:
                    cross = close_now < pivot.price <= close_prev
                    event_type = EventType.CHOCH if trend == Bias.BULLISH else EventType.BOS
                    _bias = Bias.BEARISH
                    # 寻找[pivot.bar_time, bar_time]区间最高价格的K线用来构建OrderBlock

                if cross:
                    sbs.append(
                        StructureBreak(
                            event_type=event_type,
                            bias=_bias,
                            price=close_now,
                            time=bar_time,
                            pivot=pivot,
                            ob=None
                        )
                    )
                    filled_pivot_times.append(pivot.bar_time)
                    trend = _bias

    undestroyed = [p for p in pivots if p.bar_time not in filled_pivot_times]

    return StructureBreakResult(structure_breaks=sbs, undestroyed_pivots=undestroyed)


def detect_structure_breaks(
    df: pd.DataFrame,
    pivots_high: list[Pivot],
    pivots_low: list[Pivot],
    initial_trend: Bias = Bias.NEUTRAL,
    swing_pivots_high: list[Pivot] | None = None,
    swing_pivots_low: list[Pivot] | None = None,
    filter_confluence: bool = False,
) -> tuple[list[StructureBreak], StructureState]:
    events: list[StructureBreak] = []
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
            crossover = close_now > level >= close_prev

            extra = True
            if filter_confluence and current_swing_high is not None:
                extra = current_pivot_high.price != current_swing_high.price

            if crossover and extra:
                event_type = EventType.CHOCH if trend == Bias.BEARISH else EventType.BOS
                event = StructureBreak(
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
            crossunder = close_now < level <= close_prev

            extra = True
            if filter_confluence and current_swing_low is not None:
                extra = current_pivot_low.price != current_swing_low.price

            if crossunder and extra:
                event_type = EventType.CHOCH if trend == Bias.BULLISH else EventType.BOS
                event = StructureBreak(
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
