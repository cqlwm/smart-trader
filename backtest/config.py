from dataclasses import dataclass

from model import Symbol


@dataclass(frozen=True)
class BacktestConfig:
    strategy_type: str
    strategy_config: dict[str, str | int | float | bool | list | dict]
    symbol: Symbol
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
