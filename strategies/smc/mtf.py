from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

from strategies.smc.config import SMCConfig
from strategies.smc.engine import SMCEngine, SMCResult
from strategies.smc.schemas import (
    DirectionContextOutput,
    FVGOutput,
    MTFAnalysisOutput,
    MTFMetadata,
    MTFResultOutput,
    OBOutput,
    OpportunityOutput,
    SetupZoneOutput,
    TriggerContextOutput,
)

TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
    "1M": 43200,
}

MIN_LOOKBACK = 200
MAX_LOOKBACK = 500


TIMEFRAME_ALIASES: dict[str, str] = {
    "1D": "1d",
    "3D": "3d",
    "1W": "1w",
}


def normalize_timeframe(timeframe: str) -> str:
    normalized = TIMEFRAME_ALIASES.get(timeframe, timeframe)
    if normalized in TIMEFRAME_MINUTES:
        return normalized
    raise ValueError(f"未知时间框架: {timeframe}")


def compute_lookback(
    timeframe: str,
    base_bars: int = 500,
    base_timeframe: str = "4h",
) -> int:
    try:
        timeframe = normalize_timeframe(timeframe)
    except ValueError as exc:
        raise ValueError(f"未知时间框架: {timeframe}") from exc
    try:
        base_timeframe = normalize_timeframe(base_timeframe)
    except ValueError as exc:
        raise ValueError(f"未知基准时间框架: {base_timeframe}") from exc

    base_minutes = TIMEFRAME_MINUTES[base_timeframe]
    tf_minutes = TIMEFRAME_MINUTES[timeframe]
    raw = (base_bars * base_minutes) // tf_minutes
    return max(MIN_LOOKBACK, min(MAX_LOOKBACK, raw))


class MultiTimeframeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    exchange_id: str = "binanceusdm"
    symbol: str = "BTC/USDT:USDT"
    timeframes: tuple[str, ...] = ("1h", "4h", "1d")
    base_lookback_bars: int = 500
    base_timeframe: str = "4h"
    swing_length: int = 50
    internal_length: int = 5
    equal_length: int = 3
    equal_threshold: float = 0.1
    ob_filter: str = "atr"
    ob_mitigation: str = "high_low"
    max_obs: int = 5
    fvg_min_width_atr: float = 0.1
    atr_period: int = 200
    account_balance: float = 100.0
    risk_per_trade_pct: float = 1.0
    strategy: str = "intraday"

    @field_validator("base_timeframe")
    @classmethod
    def _validate_base_timeframe(cls, v: str) -> str:
        return normalize_timeframe(v)

    @field_validator("timeframes")
    @classmethod
    def _validate_timeframes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("timeframes 不能为空")

        normalized_timeframes: list[str] = []
        seen: set[str] = set()
        for tf in v:
            normalized_tf = normalize_timeframe(tf)
            if normalized_tf in seen:
                raise ValueError(f"重复的时间框架: {tf}")
            seen.add(normalized_tf)
            normalized_timeframes.append(normalized_tf)

        return tuple(normalized_timeframes)

    def build_single_config(self, timeframe: str) -> SMCConfig:
        timeframe = normalize_timeframe(timeframe)
        lookback = compute_lookback(
            timeframe, self.base_lookback_bars, self.base_timeframe
        )
        return SMCConfig(
            exchange_id=self.exchange_id,
            symbol=self.symbol,
            timeframe=timeframe,
            lookback_bars=lookback,
            swing_length=self.swing_length,
            internal_length=self.internal_length,
            equal_length=self.equal_length,
            equal_threshold=self.equal_threshold,
            ob_filter=self.ob_filter,
            ob_mitigation=self.ob_mitigation,
            max_obs=self.max_obs,
            fvg_min_width_atr=self.fvg_min_width_atr,
            atr_period=self.atr_period,
            account_balance=self.account_balance,
            risk_per_trade_pct=self.risk_per_trade_pct,
        )


class MultiTimeframeResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    analyzed_at: str
    results: dict[str, SMCResult]
    errors: dict[str, str]
    config: MultiTimeframeConfig


class MultiTimeframeAnalyzer:
    def __init__(self, config: MultiTimeframeConfig | None = None):
        self._config = config or MultiTimeframeConfig()

    @property
    def config(self) -> MultiTimeframeConfig:
        return self._config

    async def analyze_async(self) -> MultiTimeframeResult:
        async def _run_single(timeframe: str) -> tuple[str, SMCResult]:
            single_config = self._config.build_single_config(timeframe)
            engine = SMCEngine(single_config)
            r = await asyncio.to_thread(engine.analyze)
            return timeframe, r

        tasks = [_run_single(tf) for tf in self._config.timeframes]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, SMCResult] = {}
        errors: dict[str, str] = {}

        for i, outcome in enumerate(outcomes):
            tf = self._config.timeframes[i]
            if isinstance(outcome, BaseException):
                errors[tf] = str(outcome)
            else:
                _, result = outcome
                results[tf] = result

        analyzed_at = datetime.now(timezone.utc).isoformat()

        return MultiTimeframeResult(
            symbol=self._config.symbol,
            analyzed_at=analyzed_at,
            results=results,
            errors=errors,
            config=self._config,
        )

    def analyze(self) -> MultiTimeframeResult:
        return asyncio.run(self.analyze_async())

    def to_json(self, result: MultiTimeframeResult) -> dict:
        timeframes_output: dict[str, dict] = {}
        for tf, smc_result in result.results.items():
            engine = SMCEngine(smc_result.config)
            timeframes_output[tf] = engine.to_json(smc_result)

        mtf_analysis = None
        strategy_name = result.config.strategy

        if strategy_name == "simple_intraday" and {"1w", "1d", "5m"}.issubset(result.results.keys()):
            from strategies.smc.strategy.simple import SimpleIntradayConfig, SimpleIntradayStrategy

            strategy = SimpleIntradayStrategy(
                SimpleIntradayConfig(
                    exchange_id=result.config.exchange_id,
                    symbol=result.symbol,
                    risk_per_trade_pct=result.config.risk_per_trade_pct,
                    account_balance=result.config.account_balance,
                    timeframes=result.config.timeframes,
                )
            )
            analysis = strategy.build_mtf_analysis(result)
            mtf_analysis = MTFAnalysisOutput(
                direction_layer=_serialize_direction_context(analysis.direction_context),
                setup_layer=_serialize_setup_zone(analysis.setup_zone),
                trigger_layer=_serialize_trigger_context(analysis.trigger_context),
                best_opportunity=_serialize_opportunity(analysis.best_opportunity),
                blocked_reasons=analysis.blocked_reasons,
                narrative=analysis.narrative,
            )
        elif strategy_name == "intraday" and {"1d", "4h", "1h", "15m"}.issubset(result.results.keys()):
            from strategies.smc.strategy.intraday import IntradayConfig, IntradayStrategy

            strategy = IntradayStrategy(
                IntradayConfig(
                    exchange_id=result.config.exchange_id,
                    symbol=result.symbol,
                    risk_per_trade_pct=result.config.risk_per_trade_pct,
                    account_balance=result.config.account_balance,
                    timeframes=result.config.timeframes,
                )
            )
            analysis = strategy.build_mtf_analysis(result)
            mtf_analysis = MTFAnalysisOutput(
                direction_layer=_serialize_direction_context(analysis.direction_context),
                setup_layer=_serialize_setup_zone(analysis.setup_zone),
                trigger_layer=_serialize_trigger_context(analysis.trigger_context),
                best_opportunity=_serialize_opportunity(analysis.best_opportunity),
                blocked_reasons=analysis.blocked_reasons,
                narrative=analysis.narrative,
            )

        lookback_bars = {
            tf: compute_lookback(
                tf, result.config.base_lookback_bars, result.config.base_timeframe
            )
            for tf in result.config.timeframes
        }

        return MTFResultOutput(
            multi_timeframe=True,
            symbol=result.symbol,
            analyzed_at=result.analyzed_at,
            timeframes=timeframes_output,
            mtf_analysis=mtf_analysis,
            errors=result.errors,
            metadata=MTFMetadata(
                timeframes_requested=list(result.config.timeframes),
                timeframes_completed=list(result.results.keys()),
                timeframes_failed=list(result.errors.keys()),
                lookback_bars=lookback_bars,
            ),
        ).model_dump()

    def to_enriched_json(
        self,
        result: MultiTimeframeResult,
        *,
        include_llm: bool = False,
        llm_strict: bool = False,
    ) -> dict:
        output = self.to_json(result)
        return output


def _serialize_direction_context(context) -> DirectionContextOutput | None:
    if context is None:
        return None
    return DirectionContextOutput(
        bias=context.bias.name,
        score=context.score,
        confidence=context.confidence,
        reasons=context.reasons,
        aligned_timeframes=context.aligned_timeframes,
        blocked_reasons=context.blocked_reasons,
    )


def _serialize_setup_zone(setup_zone) -> SetupZoneOutput | None:
    if setup_zone is None:
        return None
    return SetupZoneOutput(
        timeframe=setup_zone.timeframe,
        order_block=OBOutput(
            id=setup_zone.order_block.id,
            bias=setup_zone.order_block.bias.name,
            high=setup_zone.order_block.high,
            low=setup_zone.order_block.low,
            mid=setup_zone.order_block.mid,
            formed_time=setup_zone.order_block.formed_time,
            status=setup_zone.order_block.status.name,
            distance_pct=0.0,
        ),
        overlapping_fvg=None
        if setup_zone.overlapping_fvg is None
        else FVGOutput(
            id=setup_zone.overlapping_fvg.id,
            bias=setup_zone.overlapping_fvg.bias.name,
            high=setup_zone.overlapping_fvg.top,
            low=setup_zone.overlapping_fvg.bottom,
            mid=setup_zone.overlapping_fvg.mid,
            formed_time=setup_zone.overlapping_fvg.formed_time,
            status=setup_zone.overlapping_fvg.status.name,
            fill_pct=setup_zone.overlapping_fvg.fill_pct,
            width=setup_zone.overlapping_fvg.width,
            width_atr_ratio=setup_zone.overlapping_fvg.width_atr_ratio,
            mitigation_depth=setup_zone.overlapping_fvg.mitigation_depth,
            touch_count=setup_zone.overlapping_fvg.touch_count,
        ),
        zone_quality_score=setup_zone.zone_quality_score,
        zone_position_ok=setup_zone.zone_position_ok,
        distance_to_price_pct=setup_zone.distance_to_price_pct,
        reasons=setup_zone.reasons,
    )


def _serialize_trigger_context(trigger_context) -> TriggerContextOutput | None:
    if trigger_context is None:
        return None
    return TriggerContextOutput(
        timeframe=trigger_context.timeframe,
        structure_aligned=trigger_context.structure_aligned,
        last_trigger_event=trigger_context.last_trigger_event,
        liquidity_swept=trigger_context.liquidity_swept,
        fvg_reaction=trigger_context.fvg_reaction,
        trigger_score=trigger_context.trigger_score,
        reasons=trigger_context.reasons,
    )


def _serialize_opportunity(opportunity) -> OpportunityOutput | None:
    if opportunity is None:
        return None
    return OpportunityOutput(
        direction=opportunity.direction.name,
        total_score=opportunity.total_score,
        confidence=opportunity.confidence,
        passed_hard_filters=opportunity.passed_hard_filters,
        failed_filters=opportunity.failed_filters,
    )
