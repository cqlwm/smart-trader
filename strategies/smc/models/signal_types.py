from pydantic import BaseModel, ConfigDict
from enum import Enum, auto

from strategies.smc.models.types import OrderBlock, StructureEvent, Bias


class SignalStatus(Enum):
    PENDING = auto()
    FILLED = auto()
    CANCELED = auto()
    EXPIRED = auto()


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    ob: OrderBlock
    event: StructureEvent
    direction: Bias
    entry_price: float
    stop_loss: float
    take_profit: float | None
    created_bar_time: str
    status: SignalStatus


class SMCSignalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    timeframe: str
    atr_multiplier: float = 0.5
    min_rr: float = 2.0
    quantity: float = 0.001


class SignalState(BaseModel):
    model_config = ConfigDict(frozen=True)
    signals: list[Signal]
    last_swing_event_time: str
    last_internal_event_time: str
