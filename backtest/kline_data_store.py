import pandas as pd
from typing import List, Dict, Union
from pathlib import Path
from datetime import datetime, timedelta, timezone
import time
import ccxt
import log

from model import Kline, Symbol
from ccxt.base.types import ConstructorArgs

logger = log.getLogger(__name__)


class KlineDataStore:
    """K线数据存储"""

    def __init__(self):
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def _load_df(self, file_path: str, loader_fn) -> pd.DataFrame:
        """加载并校验 DataFrame，带缓存"""
        cache_key = file_path
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
        df = loader_fn(file_path)
        expected_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in expected_columns):
            raise ValueError(f"Data must contain columns: {expected_columns}")
        df['timestamp'] = df['timestamp'].astype(int)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        self.data_cache[cache_key] = df
        return df

    def _df_to_klines(self, df: pd.DataFrame, symbol: Symbol, timeframe: str) -> List[Kline]:
        """将 DataFrame 向量化转为 Kline 列表"""
        return [
            Kline(
                symbol=symbol,
                timeframe=timeframe,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                timestamp=ts,
                finished=True
            )
            for ts, open_, high, low, close, volume in zip(
                df['timestamp'].tolist(),
                df['open'].tolist(),
                df['high'].tolist(),
                df['low'].tolist(),
                df['close'].tolist(),
                df['volume'].tolist(),
            )
        ]

    def load_csv(self, file_path: str, symbol: Symbol, timeframe: str) -> List[Kline]:
        """从CSV文件加载历史K线数据（timestamp,open,high,low,close,volume）"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")
        df = self._load_df(file_path, pd.read_csv)
        klines = self._df_to_klines(df, symbol, timeframe)
        logger.info(f"Loaded {len(klines)} klines from {file_path}")
        return klines

    def clear_cache(self):
        self.data_cache.clear()
        logger.info("Data cache cleared")

    def ensure_data(
        self,
        symbol: Symbol,
        timeframe: str,
        start_time: Union[str, datetime],
        end_time: Union[str, datetime],
        data_dir: str = "data",
        offset: timedelta | None = None,
    ) -> str:
        """确保数据文件存在，若不存在则自动下载并缓存"""
        if isinstance(start_time, str):
            start_dt = datetime.fromisoformat(start_time)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        else:
            start_dt = start_time
        if isinstance(end_time, str):
            end_dt = datetime.fromisoformat(end_time)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        else:
            end_dt = end_time

        start_str = start_dt.strftime("%Y%m%d")
        end_str = end_dt.strftime("%Y%m%d")
        offset_str = f"{int(offset.total_seconds())}s" if offset else "0s"
        file_path = f"{data_dir}/{symbol.binance()}_{timeframe}_{start_str}_{offset_str}_{end_str}.csv"

        if Path(file_path).exists():
            logger.info(f"Cache hit: {file_path}")
            return file_path

        download_start = start_dt - offset if offset else start_dt
        logger.info(f"Cache miss, downloading: {file_path}")
        return self.download_historical_kline(symbol, timeframe, download_start, end_dt, file_path)

    @staticmethod
    def download_historical_kline(
        symbol: Symbol,
        interval: str,
        start_datetime: datetime,
        end_datetime: datetime,
        file_path: str
    ) -> str:
        """从Binance合约下载历史K线数据并保存为CSV"""
        exchange = ccxt.binance(ConstructorArgs(options={"defaultType": "future"}))

        start_timestamp = int(start_datetime.timestamp() * 1000)
        end_timestamp = int(end_datetime.timestamp() * 1000)

        all_ohlcv = []
        since = start_timestamp

        while since < end_timestamp:
            try:
                ohlcv = exchange.fetch_ohlcv(symbol.ccxt(), interval, since=since, limit=1000)
                if not ohlcv:
                    logger.info("No more data available")
                    break
                filtered_ohlcv = [row for row in ohlcv if row[0] <= end_timestamp]
                all_ohlcv.extend(filtered_ohlcv)
                if len(ohlcv) < 1000:
                    break
                since = ohlcv[-1][0] + 1
                logger.info(f"Fetched {len(filtered_ohlcv)} klines, total: {len(all_ohlcv)}")
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error fetching data: {e}")
                break

        if not all_ohlcv:
            raise ValueError("No data downloaded")

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = df['timestamp'].astype(int)
        df = df.sort_values('timestamp').reset_index(drop=True)

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(file_path, index=False)

        logger.info(f"Saved {len(df)} klines to {file_path}")
        return file_path
