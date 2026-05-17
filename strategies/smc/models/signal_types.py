from pydantic import BaseModel, ConfigDict
from enum import Enum, auto

from model import Symbol
from strategies.smc.models.types import OrderBlock, StructureBreak, Bias


class SignalStatus(Enum):
    PENDING = auto()
    FILLED = auto()
    CANCELED = auto()
    EXPIRED = auto()


class TradingSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    ob: OrderBlock
    event: StructureBreak
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
    swing_length: int = 10
    equal_length: int = 3
    equal_threshold: float = 0.1
    ob_filter: str = "atr"
    fvg_min_width_atr: float = 0.1
    atr_period: int = 200
    account_balance: float = 100.0
    risk_per_trade_pct: float = 1.0

class TradingSignalState(BaseModel):
    model_config = ConfigDict(frozen=True)
    signals: list[TradingSignal]
    last_swing_event_time: str
    last_internal_event_time: str
