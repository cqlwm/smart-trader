from pydantic import BaseModel, ConfigDict

import pandas as pd

from smc.config import SMCConfig
from smc.core.equal_levels import detect_equal_levels
from smc.core.fvg import detect_fvg, mitigate_fvg
from smc.core.legs import detect_legs, identify_pivots
from smc.core.order_blocks import create_order_blocks_from_events, mitigate_order_blocks
from smc.core.structure import detect_structure_breaks
from smc.core.trailing import compute_trailing_extremes
from smc.core.zones import compute_premium_discount
from smc.data.fetcher import fetch_ohlcv
from smc.indicators.atr import (
    classify_volatility,
    compute_atr,
    compute_parsed_high_low,
    compute_volatility_measure,
)
from smc.output import build_output
from smc.signal import compute_signal
from smc.types import (
    EqualLevel,
    FairValueGap,
    OrderBlock,
    PremiumDiscount,
    StructureState,
    TrailingExtremes,
)


class SMCResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    swing_state: StructureState
    internal_state: StructureState
    swing_order_blocks: list[OrderBlock]
    internal_order_blocks: list[OrderBlock]
    fvgs: list[FairValueGap]
    equal_levels: list[EqualLevel]
    trailing: TrailingExtremes
    premium_discount: PremiumDiscount
    df: pd.DataFrame
    atr_series: pd.Series
    current_atr: float
    volatility: str
    config: SMCConfig


class SMCEngine:
    def __init__(self, config: SMCConfig | None = None):
        self._config = config or SMCConfig()

    @property
    def config(self) -> SMCConfig:
        return self._config

    def analyze(self, df: pd.DataFrame | None = None) -> SMCResult:
        if df is None:
            df = fetch_ohlcv(
                self._config.symbol,
                self._config.timeframe,
                self._config.lookback_bars,
                self._config.exchange_id,
            )

        atr_series = compute_atr(df, self._config.atr_period)
        volatility_measure = compute_volatility_measure(df, self._config.ob_filter, self._config.atr_period)
        parsed_high, parsed_low = compute_parsed_high_low(df, volatility_measure)

        swing_legs = detect_legs(df["high"], df["low"], self._config.swing_length)
        internal_legs = detect_legs(df["high"], df["low"], self._config.internal_length)

        swing_pivots_h, swing_pivots_l = identify_pivots(df, swing_legs, self._config.swing_length)
        internal_pivots_h, internal_pivots_l = identify_pivots(df, internal_legs, self._config.internal_length)

        swing_events, swing_state = detect_structure_breaks(df, swing_pivots_h, swing_pivots_l)
        internal_events, internal_state = detect_structure_breaks(
            df,
            internal_pivots_h,
            internal_pivots_l,
            swing_pivots_high=swing_pivots_h,
            swing_pivots_low=swing_pivots_l,
            filter_confluence=self._config.internal_length != self._config.swing_length,
        )

        swing_obs = create_order_blocks_from_events(df, parsed_high, parsed_low, swing_events, "swing")
        internal_obs = create_order_blocks_from_events(df, parsed_high, parsed_low, internal_events, "internal")

        swing_obs = mitigate_order_blocks(swing_obs, df, self._config.ob_mitigation)
        internal_obs = mitigate_order_blocks(internal_obs, df, self._config.ob_mitigation)

        fvgs = detect_fvg(df, self._config.atr_period, self._config.fvg_min_width_atr)
        fvgs = mitigate_fvg(fvgs, df)

        equal_pivots_legs = detect_legs(df["high"], df["low"], self._config.equal_length)
        eq_pivots_h, eq_pivots_l = identify_pivots(df, equal_pivots_legs, self._config.equal_length)
        equal_levels = detect_equal_levels(eq_pivots_h, eq_pivots_l, atr_series, self._config.equal_threshold)

        trailing = compute_trailing_extremes(df, swing_pivots_h, swing_pivots_l, swing_state.trend)

        current_price = float(df["close"].iloc[-1])
        premium_discount = compute_premium_discount(trailing, current_price)

        current_atr = float(atr_series.iloc[-1])
        volatility = classify_volatility(current_atr, atr_series)

        return SMCResult(
            swing_state=swing_state,
            internal_state=internal_state,
            swing_order_blocks=swing_obs,
            internal_order_blocks=internal_obs,
            fvgs=fvgs,
            equal_levels=equal_levels,
            trailing=trailing,
            premium_discount=premium_discount,
            df=df,
            atr_series=atr_series,
            current_atr=current_atr,
            volatility=volatility,
            config=self._config,
        )

    def to_json(self, result: SMCResult) -> dict:
        signal = compute_signal(result)
        return build_output(result, signal).model_dump()

    def to_enriched_json(
        self,
        result: SMCResult,
        *,
        include_llm: bool = False,
        llm_strict: bool = False,
    ) -> dict:
        output = self.to_json(result)
        return output
