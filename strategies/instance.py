from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class InstanceStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class StrategyInstance:
    instance_id: str
    strategy_type: str
    config: dict[str, str | int | float | bool | list | dict]
    status: InstanceStatus
    created_at: datetime
    error_message: str | None = None
