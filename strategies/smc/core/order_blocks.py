import pandas as pd

from strategies.smc.core.utils import find_bar_position
from strategies.smc.models import Bias, OBStatus, OrderBlock, StructureBreak


def _create_order_block(
    df: pd.DataFrame,
    parsed_highs: pd.Series,
    parsed_lows: pd.Series,
    event: StructureBreak,
    source: str,
    ob_counter: int,
) -> OrderBlock:
    pivot = event.pivot
    start = max(find_bar_position(df, pivot.bar_time), 0)
    end = min(find_bar_position(df, event.time), len(df))

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
        status=OBStatus.UNTESTED,
        source=source,
        source_event=event,
    )


def create_order_blocks_from_events(
    df: pd.DataFrame,
    parsed_highs: pd.Series,
    parsed_lows: pd.Series,
    events: list[StructureBreak],
    source: str,
) -> list[OrderBlock]:
    blocks: list[OrderBlock] = []
    for i, event in enumerate(events):
        ob = _create_order_block(df, parsed_highs, parsed_lows, event, source, i + 1)
        blocks.append(ob)
    return blocks


def _is_order_block_tested(ob: OrderBlock, bar_high: float, bar_low: float) -> bool:
    if ob.bias == Bias.BULLISH:
        return ob.low < bar_low < ob.high
    else:
        return ob.low < bar_high < ob.high


def _is_order_block_mitigated(ob: OrderBlock, bar_high: float, bar_low: float) -> bool:
    if ob.bias == Bias.BULLISH:
        return bar_low <= ob.low
    else:
        return bar_high >= ob.high


def mitigate_order_blocks(order_blocks: list[OrderBlock], df: pd.DataFrame) -> list[OrderBlock]:
    active = list(order_blocks)
    datetime_strs = df["datetime"].astype(str).tolist()

    for bar in range(len(df)):
        bar_high = df["high"].iloc[bar]
        bar_low = df["low"].iloc[bar]
        bar_time = datetime_strs[bar]

        remaining: list[OrderBlock] = []
        for ob in active:
            if ob.source_event and ob.source_event.time >= bar_time:
                remaining.append(ob)
            else:
                if _is_order_block_mitigated(ob, bar_high, bar_low):
                    pass # 忽略已经被缓解的OB
                elif _is_order_block_tested(ob, bar_high, bar_low):
                    remaining.append(ob.model_copy(update={"status": OBStatus.TESTED}))
                else:
                    remaining.append(ob)

        active = remaining

    return active
