import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta

from persistence.kline_data_store import KlineDataStore
from model import Symbol, Kline
from strategies.smc.core.legs import identify_pivots, detect_legs
from strategies.smc.models.types import Pivot


SYMBOL = Symbol(base="BTC", quote="USDT")
TIMEFRAME = "1d"
START = "2026-01-14"
END = "2026-05-08"


def _to_df(klines: list[Kline]) -> pd.DataFrame:
    return pd.DataFrame({
        "high": [k.high for k in klines],
        "low": [k.low for k in klines],
        "datetime": [k.datetime for k in klines],
    })


@pytest.fixture(scope="module")
def btcusdt_data() -> dict:
    store = KlineDataStore()
    path = store.ensure_data(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        start=START,
        end=END,
        offset=timedelta(hours=6),
    )
    klines = store.load_csv(path, SYMBOL, TIMEFRAME)
    df = _to_df(klines)
    return {"df": df, "klines": klines}

class TestPivotIdentification:
    @pytest.fixture
    def legs(self, btcusdt_data: dict) -> pd.Series:
        df = btcusdt_data["df"]
        return detect_legs(df["high"], df["low"], size=10)

    @pytest.fixture
    def pivots(self, btcusdt_data: dict, legs: pd.Series) -> tuple[list[Pivot], list[Pivot]]:
        return identify_pivots(btcusdt_data["df"], legs, size=10)

    def test_pivot_type_fields(self, pivots: tuple[list[Pivot], list[Pivot]]) -> None:
        pivot_highs, pivot_lows = pivots
        for p in pivot_highs:
            assert p.is_high is True
        for p in pivot_lows:
            assert p.is_high is False