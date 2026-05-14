import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta

from persistence.kline_data_store import KlineDataStore
from model import Symbol, Kline
from strategies.smc.core.legs import pivots
from strategies.smc.core.structure import detect_structure_breaks_v2
from strategies.smc.models.types import Pivot


SYMBOL = Symbol(base="BTC", quote="USDT")
TIMEFRAME = "1d"
START = "2026-01-14"
END = "2026-05-08"


def _to_df(klines: list[Kline]) -> pd.DataFrame:
    return pd.DataFrame({
        "close": [k.close for k in klines],
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


    def test_detect_structure_breaks_v2(self, btcusdt_data: dict) -> None:
        df = btcusdt_data['df']
        p = pivots(df, 10)
        # assert p.all == '''
        # [Pivot(price=59800.0, bar_time='2026-02-06 00:00:00+00:00', label='LL', is_high=False), Pivot(price=72300.0, bar_time='2026-02-08 00:00:00+00:00', label='HH', is_high=True), Pivot(price=65081.0, bar_time='2026-02-12 00:00:00+00:00', label='HL', is_high=False), Pivot(price=70938.5, bar_time='2026-02-15 00:00:00+00:00', label='LH', is_high=True), Pivot(price=62401.7, bar_time='2026-02-24 00:00:00+00:00', label='LL', is_high=False), Pivot(price=74046.5, bar_time='2026-03-04 00:00:00+00:00', label='HH', is_high=True), Pivot(price=65569.2, bar_time='2026-03-08 00:00:00+00:00', label='HL', is_high=False), Pivot(price=75998.9, bar_time='2026-03-17 00:00:00+00:00', label='HH', is_high=True), Pivot(price=64918.2, bar_time='2026-03-29 00:00:00+00:00', label='LL', is_high=False)]
        # '''
        sbs = detect_structure_breaks_v2(df, p.all)
        print(sbs)

