from dataclasses import dataclass

from model import Symbol


@dataclass(frozen=True)
class BacktestConfig:
    symbol: Symbol
    timeframe: str
    start_date: str
    end_date: str
    config_path: str = "strategies.yaml"
    initial_balance: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
    extra_timeframes: tuple[str, ...] = ()
    data_dir: str = "data"
    start_index: int = 300
