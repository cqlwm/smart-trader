from pydantic import BaseModel


class KlineRequest(BaseModel):
    symbol: str
    timeframe: str
    start_date: str
    end_date: str


class KlinePointResponse(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class KlineDataResponse(BaseModel):
    symbol: str
    timeframe: str
    klines: list[KlinePointResponse]
