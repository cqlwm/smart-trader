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
