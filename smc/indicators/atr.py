import numpy as np
import pandas as pd


def compute_true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def compute_atr(df: pd.DataFrame, period: int = 200) -> pd.Series:
    tr = compute_true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_cumulative_mean_range(df: pd.DataFrame) -> pd.Series:
    tr = compute_true_range(df)
    cumsum = tr.cumsum()
    bar_count = pd.Series(np.arange(1, len(df) + 1), index=df.index, dtype=float)
    return cumsum / bar_count


def compute_volatility_measure(df: pd.DataFrame, method: str = "atr", period: int = 200) -> pd.Series:
    if method == "atr":
        return compute_atr(df, period)
    return compute_cumulative_mean_range(df)


def compute_parsed_high_low(df: pd.DataFrame, volatility: pd.Series) -> tuple[pd.Series, pd.Series]:
    high_vol = (df["high"] - df["low"]) >= (2 * volatility)
    parsed_high = df["high"].where(~high_vol, df["low"])
    parsed_low = df["low"].where(~high_vol, df["high"])
    return parsed_high, parsed_low


def classify_volatility(current_atr: float, atr_series: pd.Series) -> str:
    median_atr = atr_series.median()
    if current_atr > 1.5 * median_atr:
        return "HIGH"
    if current_atr < 0.5 * median_atr:
        return "LOW"
    return "NORMAL"
