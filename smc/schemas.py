from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Signal schemas (smc/signal.py)
# ---------------------------------------------------------------------------
class Entry(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: float
    type: str
    zone_high: float
    zone_low: float


class StopLoss(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: float
    basis: str
    distance_pct: float


class Target(BaseModel):
    model_config = ConfigDict(frozen=True)
    level: int
    price: float
    rr: float
    basis: str


class PositionSizing(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_balance: float
    risk_per_trade_pct: float
    risk_amount: float
    position_value: float
    leverage: float
    recommended_leverage: float


class SignalInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: str
    confidence: float
    reason: list[str] = Field(default_factory=list)
    invalidation: list[str] = Field(default_factory=list)


class ExecutionInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry: Entry
    stop_loss: StopLoss
    targets: list[Target] = Field(default_factory=list)
    position_sizing: PositionSizing


class SignalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    signal: SignalInfo
    execution: ExecutionInfo


# ---------------------------------------------------------------------------
# Output schemas (smc/output.py)
# ---------------------------------------------------------------------------
class EventOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    bias: str
    price: float
    time: str


class PivotOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: float
    time: str
    label: str


class StructureOutputState(BaseModel):
    model_config = ConfigDict(frozen=True)
    trend: str
    last_event: EventOutput | None = None
    pivot_high: PivotOutput | None = None
    pivot_low: PivotOutput | None = None


class StructureOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    swing: StructureOutputState
    internal: StructureOutputState


class OBOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    bias: str
    high: float
    low: float
    mid: float
    formed_time: str
    status: str
    distance_pct: float


class FVGOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    bias: str
    high: float
    low: float
    mid: float
    formed_time: str
    status: str
    fill_pct: float
    width: float
    width_atr_ratio: float
    mitigation_depth: float
    touch_count: int


class EqualLevelOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    price: float
    time: str
    touches: int


class PremiumDiscountOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    premium_zone_high: float
    premium_zone_low: float
    equilibrium: float
    discount_zone_high: float
    discount_zone_low: float
    current_position: str


class LiquidityPool(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    price: float
    strength: str


class OrderBlocksOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    swing: list[OBOutput] = Field(default_factory=list)
    internal: list[OBOutput] = Field(default_factory=list)


class ZonesOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    order_blocks: OrderBlocksOutput
    fair_value_gaps: list[FVGOutput] = Field(default_factory=list)
    equal_highs_lows: list[EqualLevelOutput] = Field(default_factory=list)
    premium_discount: PremiumDiscountOutput
    liquidity_pools: list[LiquidityPool] = Field(default_factory=list)


class SwingLabelsOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_pivot_type: str
    sequence: list[str] = Field(default_factory=list)


class ContextOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    timeframe: str
    timestamp: str
    current_price: float
    atr: float
    volatility: str
    swing_labels: SwingLabelsOutput


class SummaryOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    market_narrative: str
    confluence_score: dict[str, int | str] = Field(default_factory=dict)
    risk_note: str


class FullOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    structure: StructureOutput
    zones: ZonesOutput
    signal: SignalInfo
    execution: ExecutionInfo
    context: ContextOutput
    summary: SummaryOutput


# ---------------------------------------------------------------------------
# Strategy output schemas (smc/strategy/intraday.py)
# ---------------------------------------------------------------------------
class ConditionOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    passed: bool
    description: str
    weight: int
    is_hard_filter: bool = False


class DirectionContextOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    bias: str
    score: int
    confidence: int
    reasons: list[str] = Field(default_factory=list)
    aligned_timeframes: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class SetupZoneOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    timeframe: str
    order_block: OBOutput
    overlapping_fvg: FVGOutput | None = None
    zone_quality_score: int
    zone_position_ok: bool
    distance_to_price_pct: float
    reasons: list[str] = Field(default_factory=list)


class TriggerContextOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    timeframe: str
    structure_aligned: bool
    last_trigger_event: str | None = None
    liquidity_swept: bool
    fvg_reaction: bool
    trigger_score: int
    reasons: list[str] = Field(default_factory=list)


class OpportunityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction: str
    total_score: int
    confidence: int
    passed_hard_filters: bool
    failed_filters: list[str] = Field(default_factory=list)


class TradePlanOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    position_size: float
    risk_reward: float


class IntradaySignalOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy: str = "intraday"
    symbol: str
    action: str
    confidence: int
    current_price: float
    analyzed_at: str
    trade_plan: TradePlanOutput | None = None
    conditions: list[ConditionOutput] = Field(default_factory=list)
    direction_context: DirectionContextOutput
    setup_zone: SetupZoneOutput | None = None
    trigger_context: TriggerContextOutput | None = None
    opportunity_score: int
    blocked_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MTF output schemas (smc/mtf.py)
# ---------------------------------------------------------------------------
class MTFAnalysisOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction_layer: DirectionContextOutput | None = None
    setup_layer: SetupZoneOutput | None = None
    trigger_layer: TriggerContextOutput | None = None
    best_opportunity: OpportunityOutput | None = None
    blocked_reasons: list[str] = Field(default_factory=list)
    narrative: str


class MTFMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    timeframes_requested: list[str] = Field(default_factory=list)
    timeframes_completed: list[str] = Field(default_factory=list)
    timeframes_failed: list[str] = Field(default_factory=list)
    lookback_bars: dict[str, int] = Field(default_factory=dict)


class MTFResultOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    multi_timeframe: bool = True
    symbol: str
    analyzed_at: str
    timeframes: dict[str, dict] = Field(default_factory=dict)
    mtf_analysis: MTFAnalysisOutput | None = None
    errors: dict[str, str] = Field(default_factory=dict)
    metadata: MTFMetadata
