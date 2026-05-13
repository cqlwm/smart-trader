import pandas as pd


def find_bar_position(df: pd.DataFrame, time_str: str) -> int:
    indices = df.index[df["datetime"].astype(str) == time_str]
    if len(indices) == 0:
        raise ValueError(f"No bar found with datetime {time_str}")
    return int(indices[0])
