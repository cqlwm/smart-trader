import pandas as pd

from smc.types import Bias, OBStatus, OrderBlock, StructureEvent


def create_order_block(
    df: pd.DataFrame,
    parsed_highs: pd.Series,
    parsed_lows: pd.Series,
    event: StructureEvent,
    source: str,
    ob_counter: int,
) -> OrderBlock:
    pivot = event.pivot
    start = max(pivot.bar_index, 0)
    end = min(event.bar_index, len(df))

    if start >= end:
        end = start + 1

    if event.bias == Bias.BULLISH:
        segment = parsed_lows.iloc[start:end]
        target_idx = segment.idxmin()
    else:
        segment = parsed_highs.iloc[start:end]
        target_idx = segment.idxmax()

    ob_high = parsed_highs.iloc[target_idx]
    ob_low = parsed_lows.iloc[target_idx]
    formed_time = str(df["datetime"].iloc[target_idx])

    return OrderBlock(
        id=f"{source}OB_{ob_counter:03d}",
        bias=event.bias,
        high=float(ob_high),
        low=float(ob_low),
        mid=float((ob_high + ob_low) / 2),
        formed_time=formed_time,
        formed_index=int(target_idx),
        status=OBStatus.UNTESTED,
        source=source,
    )


def create_order_blocks_from_events(
    df: pd.DataFrame,
    parsed_highs: pd.Series,
    parsed_lows: pd.Series,
    events: list[StructureEvent],
    source: str,
) -> list[OrderBlock]:
    blocks: list[OrderBlock] = []
    for i, event in enumerate(events):
        ob = create_order_block(df, parsed_highs, parsed_lows, event, source, i + 1)
        blocks.append(ob)
    return blocks


def mitigate_order_blocks(
    order_blocks: list[OrderBlock],
    df: pd.DataFrame,
    mitigation: str = "high_low",
) -> list[OrderBlock]:
    active = list(order_blocks)

    for bar in range(len(df)):
        bar_high = df["high"].iloc[bar]
        bar_low = df["low"].iloc[bar]
        bar_close = df["close"].iloc[bar]

        if mitigation == "close":
            bull_source = bar_close
            bear_source = bar_close
        else:
            bull_source = bar_low
            bear_source = bar_high

        remaining: list[OrderBlock] = []
        for ob in active:
            if ob.formed_index >= bar:
                remaining.append(ob)
                continue

            if ob.bias == Bias.BEARISH and bear_source > ob.high:
                continue
            if ob.bias == Bias.BULLISH and bull_source < ob.low:
                continue

            if _is_order_block_tested(ob, bar_high, bar_low):
                remaining.append(ob.model_copy(update={"status": OBStatus.TESTED}))
                continue
            remaining.append(ob)

        active = remaining

    return active


def _is_order_block_tested(ob: OrderBlock, bar_high: float, bar_low: float) -> bool:
    return bar_high >= ob.low and bar_low <= ob.high
