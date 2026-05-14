import pandas as pd

from strategies.smc.models import Pivot, Pivots


def _detect_legs(highs: pd.Series, lows: pd.Series, size: int) -> pd.Series:
    rolling_high = highs.rolling(size).max()
    rolling_low = lows.rolling(size).min()

    shifted_high = highs.shift(size)
    shifted_low = lows.shift(size)

    legs = pd.Series(index=highs.index, dtype="Int64")

    new_bearish = shifted_high > rolling_high
    new_bullish = shifted_low < rolling_low

    legs[new_bullish] = 1
    legs[new_bearish] = 0

    legs = legs.ffill().fillna(0).astype(int)
    return legs


def _label_pivot(current_price: float, last_price: float | None, is_high: bool) -> str:
    if last_price is None:
        return "HH" if is_high else "LL"
    if is_high:
        return "HH" if current_price > last_price else "LH"
    return "LL" if current_price < last_price else "HL"


def _identify_pivots(df: pd.DataFrame, legs: pd.Series, size: int) -> tuple[list[Pivot], list[Pivot]]:
    leg_changes = legs.diff()
    pivot_highs: list[Pivot] = []
    pivot_lows: list[Pivot] = []

    for i in range(1, len(legs)):
        change = leg_changes.iloc[i]
        if change == 0 or pd.isna(change):
            continue

        pivot_bar = i - size
        if pivot_bar < 0:
            continue

        if change == -1:
            price = df["high"].iloc[pivot_bar]
            ts = str(df["datetime"].iloc[pivot_bar])
            last_price = pivot_highs[-1].price if pivot_highs else None
            label = _label_pivot(price, last_price, is_high=True)
            pivot_highs.append(Pivot(price=price, bar_time=ts, label=label, is_high=True))
        elif change == 1:
            price = df["low"].iloc[pivot_bar]
            ts = str(df["datetime"].iloc[pivot_bar])
            last_price = pivot_lows[-1].price if pivot_lows else None
            label = _label_pivot(price, last_price, is_high=False)
            pivot_lows.append(Pivot(price=price,bar_time=ts,label=label,is_high=False))

    return pivot_highs, pivot_lows


def pivots(df: pd.DataFrame, length: int) -> Pivots:
    legs = _detect_legs(df["high"], df["low"], length)
    pivot_highs, pivot_lows = _identify_pivots(df, legs, length)
    combined_pivots = pivot_highs + pivot_lows
    combined_pivots.sort(key=lambda x: x.bar_time)
    return Pivots(
        highs=pivot_highs,
        lows=pivot_lows,
        all=combined_pivots
    )

