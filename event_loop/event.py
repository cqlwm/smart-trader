from dataclasses import dataclass

from model import Kline


@dataclass(frozen=True)
class Event:
    timestamp: int


@dataclass(frozen=True)
class KlineEvent(Event):
    kline: Kline


@dataclass(frozen=True)
class ScheduledEvent(Event):
    schedule_name: str
