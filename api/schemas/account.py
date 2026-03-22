from pydantic import BaseModel

class Balance(BaseModel):
    asset: str
    free: float
    locked: float

class Position(BaseModel):
    symbol: str
    positionAmt: float
    entryPrice: float
    unRealizedProfit: float
    leverage: int
