import pandas as pd

from strategies.smc.types import Bias, Pivot, TrailingExtremes


def compute_trailing_extremes(
    df: pd.DataFrame,
    swing_pivots_high: list[Pivot],
    swing_pivots_low: list[Pivot],
    trend: Bias,
) -> TrailingExtremes:
    top = df["high"].iloc[0]
    bottom = df["low"].iloc[0]
    top_time = str(df["datetime"].iloc[0])
    bottom_time = str(df["datetime"].iloc[0])

    high_idx = 0
    low_idx = 0

    for bar in range(len(df)):
        while high_idx < len(swing_pivots_high) and swing_pivots_high[high_idx].bar_index <= bar:
            pivot = swing_pivots_high[high_idx]
            top = pivot.price
            top_time = pivot.bar_time
            high_idx += 1

        while low_idx < len(swing_pivots_low) and swing_pivots_low[low_idx].bar_index <= bar:
            pivot = swing_pivots_low[low_idx]
            bottom = pivot.price
            bottom_time = pivot.bar_time
            low_idx += 1

        bar_high = df["high"].iloc[bar]
        bar_low = df["low"].iloc[bar]

        if bar_high > top:
            top = float(bar_high)
            top_time = str(df["datetime"].iloc[bar])

        if bar_low < bottom:
            bottom = float(bar_low)
            bottom_time = str(df["datetime"].iloc[bar])

    if trend == Bias.BEARISH:
        top_label = "Strong High"
        bottom_label = "Weak Low"
    else:
        top_label = "Weak High"
        bottom_label = "Strong Low"

    return TrailingExtremes(
        top=top,
        bottom=bottom,
        top_time=top_time,
        bottom_time=bottom_time,
        top_label=top_label,
        bottom_label=bottom_label,
    )
