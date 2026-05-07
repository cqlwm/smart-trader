import pandas as pd

from strategies.smc.indicators.atr import compute_atr
from strategies.smc.types import Bias, FVGStatus, FairValueGap


def detect_fvg(
    df: pd.DataFrame,
    atr_period: int = 200,
    min_width_atr: float = 0.1,
) -> list[FairValueGap]:
    fvgs: list[FairValueGap] = []

    low_curr = df["low"]
    high_2ago = df["high"].shift(2)
    close_1ago = df["close"].shift(1)
    high_curr = df["high"]
    low_2ago = df["low"].shift(2)
    atr = compute_atr(df, atr_period)

    bull_mask = (low_curr > high_2ago) & (close_1ago > high_2ago)
    bear_mask = (high_curr < low_2ago) & (close_1ago < low_2ago)

    bull_top = low_curr
    bull_bottom = high_2ago
    bull_width = bull_top - bull_bottom

    bear_top = low_2ago
    bear_bottom = high_curr
    bear_width = bear_top - bear_bottom

    bull_mask = bull_mask & (bull_width >= (min_width_atr * atr))
    bear_mask = bear_mask & (bear_width >= (min_width_atr * atr))

    bull_counter = 0
    for i in bull_mask[bull_mask].index:
        bull_counter += 1
        top = float(bull_top.iloc[i])
        bottom = float(bull_bottom.iloc[i])
        width = float(bull_width.iloc[i])
        atr_value = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
        fvgs.append(
            FairValueGap(
                id=f"FVG_BULL_{bull_counter:03d}",
                bias=Bias.BULLISH,
                top=top,
                bottom=bottom,
                mid=(top + bottom) / 2,
                formed_time=str(df["datetime"].iloc[i]),
                formed_index=int(i),
                status=FVGStatus.OPEN,
                fill_pct=0.0,
                width=width,
                width_atr_ratio=width / atr_value if atr_value > 0 else 0.0,
                mitigation_depth=0.0,
                touch_count=0,
            )
        )

    bear_counter = 0
    for i in bear_mask[bear_mask].index:
        bear_counter += 1
        top = float(bear_top.iloc[i])
        bottom = float(bear_bottom.iloc[i])
        width = float(bear_width.iloc[i])
        atr_value = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
        fvgs.append(
            FairValueGap(
                id=f"FVG_BEAR_{bear_counter:03d}",
                bias=Bias.BEARISH,
                top=top,
                bottom=bottom,
                mid=(top + bottom) / 2,
                formed_time=str(df["datetime"].iloc[i]),
                formed_index=int(i),
                status=FVGStatus.OPEN,
                fill_pct=0.0,
                width=width,
                width_atr_ratio=width / atr_value if atr_value > 0 else 0.0,
                mitigation_depth=0.0,
                touch_count=0,
            )
        )

    fvgs.sort(key=lambda f: f.formed_index)
    return fvgs


def mitigate_fvg(
    fvgs: list[FairValueGap],
    df: pd.DataFrame,
) -> list[FairValueGap]:
    mitigated: list[FairValueGap] = []

    for fvg in fvgs:
        touch_count = 0
        mitigation_depth = 0.0

        if fvg.width > 0:
            for bar in range(fvg.formed_index + 1, len(df)):
                if fvg.bias == Bias.BULLISH:
                    price = float(df["low"].iloc[bar])
                    if price <= fvg.top:
                        touch_count += 1
                        mitigation_depth = max(mitigation_depth, (fvg.top - price) / fvg.width)
                else:
                    price = float(df["high"].iloc[bar])
                    if price >= fvg.bottom:
                        touch_count += 1
                        mitigation_depth = max(mitigation_depth, (price - fvg.bottom) / fvg.width)

                if mitigation_depth >= 1.0:
                    break

        if mitigation_depth >= 1.0:
            status = FVGStatus.FILLED
        elif touch_count > 0:
            status = FVGStatus.PARTIAL
        else:
            status = FVGStatus.OPEN

        mitigated.append(
            FairValueGap(
                id=fvg.id,
                bias=fvg.bias,
                top=fvg.top,
                bottom=fvg.bottom,
                mid=fvg.mid,
                formed_time=fvg.formed_time,
                formed_index=fvg.formed_index,
                status=status,
                fill_pct=min(mitigation_depth, 1.0) * 100,
                width=fvg.width,
                width_atr_ratio=fvg.width_atr_ratio,
                mitigation_depth=mitigation_depth,
                touch_count=touch_count,
            )
        )

    return mitigated
