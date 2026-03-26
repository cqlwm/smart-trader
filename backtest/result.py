from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BacktestResult:
    analysis: dict[str, Any]
    trade_history: list[dict[str, Any]]
    final_balance: float
    report: str
