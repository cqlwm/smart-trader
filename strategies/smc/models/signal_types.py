from pydantic import BaseModel, ConfigDict
from enum import Enum, auto

from model import Symbol
from strategies.smc.models.config import SMCConfig
from strategies.smc.models.types import OrderBlock, StructureEvent, Bias


class SignalStatus(Enum):
    PENDING = auto()
    FILLED = auto()
    CANCELED = auto()
    EXPIRED = auto()


class TradingSignal(BaseModel):
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


class SMCStrategyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: Symbol
    timeframe: str
    quantity: float
    smc_config: SMCConfig = SMCConfig()


class TradingSignalState(BaseModel):
    model_config = ConfigDict(frozen=True)
    signals: list[TradingSignal]
    last_swing_event_time: str
    last_internal_event_time: str
