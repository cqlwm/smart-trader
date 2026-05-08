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