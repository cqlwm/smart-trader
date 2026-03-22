from typing import List
from pydantic import BaseModel

class StrategyInfo(BaseModel):
    name: str
    symbols: List[str]
    timeframes: List[str]

class StrategyStatus(BaseModel):
    is_running: bool
    strategies: List[StrategyInfo]
