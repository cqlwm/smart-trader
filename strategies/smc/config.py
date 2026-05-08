from pydantic import BaseModel, ConfigDict


class SMCConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    lookback_bars: int = 500
    swing_length: int = 50
    internal_length: int = 5
    equal_length: int = 3
    equal_threshold: float = 0.1
    ob_filter: str = "atr"
    max_obs: int = 5
    fvg_min_width_atr: float = 0.1
    atr_period: int = 200
    account_balance: float = 100.0
    risk_per_trade_pct: float = 1.0
