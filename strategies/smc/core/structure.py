import pandas as pd
from pandas import DataFrame

from strategies.smc.models import Bias, EventType, OBStatus, OrderBlock, Pivot, Pivots, StructureBreak
from strategies.smc.models.types import StructureInfo


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


def _identify_pivots(df: pd.DataFrame, legs: pd.Series, swing_length: int) -> Pivots:
    leg_changes = legs.diff()
    pivot_highs: list[Pivot] = []
    pivot_lows: list[Pivot] = []

    for i in range(1, len(legs)):
        change = leg_changes.iloc[i]
        if change == 0 or pd.isna(change):
            continue

        pivot_bar = i - swing_length
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

    combined_pivots = pivot_highs + pivot_lows
    combined_pivots.sort(key=lambda x: x.bar_time)
    return Pivots(
        highs=pivot_highs,
        lows=pivot_lows,
        all=combined_pivots
    )

def detect_structure_breaks(df: pd.DataFrame, _pivots: list[Pivot], initial_trend: Bias = Bias.NEUTRAL) -> StructureInfo:
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
        bar_low = low_prices.iloc[bar_i]
        bar_high = high_prices.iloc[bar_i]

        for sb_i, sb in enumerate(sbs):
            ob = sb.ob
            if ob is None or ob.status == OBStatus.MITIGATED:
                continue
            if bar_low > ob.high or bar_high < ob.low:
                continue
            if sb.bias == Bias.BULLISH and bar_low <= ob.low:
                new_status = OBStatus.MITIGATED
            elif sb.bias == Bias.BEARISH and bar_high >= ob.high:
                new_status = OBStatus.MITIGATED
            else:
                new_status = OBStatus.TESTED
            if new_status != ob.status:
                sbs[sb_i] = sb.model_copy(update={"ob": ob.model_copy(update={"status": new_status})})

        for pivot in _pivots:
            if pivot.bar_time <= bar_time and pivot.bar_time not in filled_pivot_times:
                pivot_idx = datetime_strs.index(pivot.bar_time)
                range_slice = slice(pivot_idx, bar_i + 1)

                if pivot.is_high:
                    cross = close_now > pivot.price >= close_prev
                    event_type = EventType.CHOCH if trend == Bias.BEARISH else EventType.BOS
                    _bias = Bias.BULLISH
                    target_idx = low_prices.iloc[range_slice].idxmin()
                else:
                    cross = close_now < pivot.price <= close_prev
                    event_type = EventType.CHOCH if trend == Bias.BULLISH else EventType.BOS
                    _bias = Bias.BEARISH
                    target_idx = high_prices.iloc[range_slice].idxmax()

                if cross:
                    ob_high = float(high_prices[target_idx])
                    ob_low = float(low_prices[target_idx])
                    ob_time = datetime_strs[int(target_idx)]
                    ob = OrderBlock(
                        id=f"{event_type}_OB_{ob_time}",
                        bias=_bias,
                        high=ob_high,
                        low=ob_low,
                        mid=(ob_high + ob_low) / 2,
                        formed_time=datetime_strs[int(target_idx)],
                        status=OBStatus.UNTESTED,
                        source="OB",
                    )
                    sbs.append(
                        StructureBreak(
                            event_type=event_type,
                            bias=_bias,
                            price=close_now,
                            time=bar_time,
                            pivot=pivot,
                            ob=ob,
                        )
                    )
                    filled_pivot_times.append(pivot.bar_time)
                    trend = _bias

    unbreak_pivots = [p for p in _pivots if p.bar_time not in filled_pivot_times]

    return StructureInfo(structure_breaks=sbs, unbreak_pivots=unbreak_pivots)


def structure_info(df: DataFrame, swing_length: int) -> StructureInfo:
    _legs = _detect_legs(df["high"], df["low"], swing_length)
    _pivots = _identify_pivots(df, _legs, swing_length)
    return detect_structure_breaks(df, _pivots.all)

