from pydantic import BaseModel, ConfigDict

from enum import Enum, auto


class Bias(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


class EventType(Enum):
    BOS = auto()
    CHOCH = auto()


class ZonePosition(Enum):
    PREMIUM = auto()
    EQUILIBRIUM = auto()
    DISCOUNT = auto()


class OBStatus(Enum):
    UNTESTED = auto()
    TESTED = auto()
    MITIGATED = auto()


class FVGStatus(Enum):
    OPEN = auto()
    PARTIAL = auto()
    FILLED = auto()


class Pivot(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: float
    bar_time: str
    label: str
    is_high: bool


class StructureEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_type: EventType
    bias: Bias
    price: float
    time: str
    pivot: Pivot


class StructureState(BaseModel):
    model_config = ConfigDict(frozen=True)
    trend: Bias
    last_event: StructureEvent | None
    pivot_high: Pivot | None
    pivot_low: Pivot | None
    swing_labels: list[str]


class OrderBlock(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    bias: Bias
    high: float
    low: float
    mid: float
    formed_time: str
    status: OBStatus
    source: str
    source_event: StructureEvent | None = None


class FairValueGap(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    bias: Bias
    top: float
    bottom: float
    mid: float
    formed_time: str
    status: FVGStatus
    fill_pct: float
    width: float
    width_atr_ratio: float
    mitigation_depth: float
    touch_count: int


class EqualLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    level_type: str
    price: float
    time: str
    touches: int


class TrailingExtremes(BaseModel):
    model_config = ConfigDict(frozen=True)
    top: float
    bottom: float
    top_time: str
    bottom_time: str
    top_label: str
    bottom_label: str


class PremiumDiscount(BaseModel):
    model_config = ConfigDict(frozen=True)
    premium_zone_high: float
    premium_zone_low: float
    equilibrium: float
    discount_zone_high: float
    discount_zone_low: float
    current_position: ZonePosition