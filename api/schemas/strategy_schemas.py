from pydantic import BaseModel


class CreateStrategyRequest(BaseModel):
    strategy_type: str
    config: dict[str, str | int | float | bool | list | dict]


class StrategyResponse(BaseModel):
    instance_id: str
    strategy_type: str
    status: str
    config: dict[str, str | int | float | bool | list | dict]
    created_at: str
    error_message: str | None = None


class StrategyTypeInfo(BaseModel):
    name: str
