import pandas as pd

from strategies.smc.core.utils import find_bar_position
from strategies.smc.models import EqualLevel, Pivot


def detect_equal_levels(
    pivots_high: list[Pivot],
    pivots_low: list[Pivot],
    atr_series: pd.Series,
    df: pd.DataFrame,
    threshold: float = 0.1,
) -> list[EqualLevel]:
    levels: list[EqualLevel] = []

    levels.extend(_detect_for_pivots(pivots_high, atr_series, df, threshold, "EQH"))
    levels.extend(_detect_for_pivots(pivots_low, atr_series, df, threshold, "EQL"))

    levels.sort(key=lambda el: el.time)
    return levels


def _detect_for_pivots(
    pivots: list[Pivot],
    atr_series: pd.Series,
    df: pd.DataFrame,
    threshold: float,
    level_type: str,
) -> list[EqualLevel]:
    results: list[EqualLevel] = []

    for i in range(1, len(pivots)):
        current = pivots[i]
        previous = pivots[i - 1]

        atr_idx = min(find_bar_position(df, current.bar_time), len(atr_series) - 1)
        atr_val = atr_series.iloc[atr_idx]

        if pd.isna(atr_val) or atr_val == 0:
            continue

        if abs(current.price - previous.price) < threshold * atr_val:
            avg_price = (current.price + previous.price) / 2
            results.append(
                EqualLevel(
                    level_type=level_type,
                    price=avg_price,
                    time=current.bar_time,
                    touches=2,
                )
            )

    return results
