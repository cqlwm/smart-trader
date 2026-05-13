import pandas as pd


def find_bar_position(df: pd.DataFrame, time_str: str) -> int:
    mask = df["datetime"] == pd.Timestamp(time_str)
    indices = df.index[mask]
    if len(indices) == 0:
        raise ValueError(f"No bar found with datetime {time_str}")
    return int(indices[0])
