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
        # file_path:df
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']

    @staticmethod
    def _df_to_klines(df: pd.DataFrame, symbol: Symbol, timeframe: str) -> List[Kline]:
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

        cache_key = file_path
        if cache_key in self.data_cache:
            df = self.data_cache[cache_key]
        else:
            df = pd.read_csv(file_path)

        if not all(col in df.columns for col in self.columns):
            raise ValueError(f"Data must contain columns: {self.columns}")
        df['timestamp'] = df['timestamp'].astype(int)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        self.data_cache[cache_key] = df

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
        def to_datetime(_t: Union[str, datetime]) -> datetime:
            if isinstance(_t, str):
                _r = datetime.fromisoformat(_t)
                if _r.tzinfo is None:
                    _r = _r.replace(tzinfo=timezone.utc)
            else:
                _r = _t
            return _r

        start_dt = to_datetime(start_time)
        end_dt= to_datetime(end_time)

        file_path = (
            f"{data_dir}/{symbol.binance()}_{timeframe}_"
            f"{start_dt.strftime('%Y%m%d')}_"
            f"{int(offset.total_seconds()) if offset else '0'}s_"
            f"{end_dt.strftime('%Y%m%d')}.csv"
        )

        if Path(file_path).exists():
            logger.info(f"Cache hit: {file_path}")
            return file_path
        else:
            logger.info(f"Cache miss, downloading: {file_path}")

        start_dt = start_dt - offset if offset else start_dt
        df = self._fetch_klines(symbol, timeframe, int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000))

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(file_path, index=False)

        logger.info(f"Saved {len(df)} klines to {file_path}")

        self.data_cache[file_path] = df

        return file_path

    def _fetch_klines(self, symbol: Symbol, interval: str, start: int, end: int) -> pd.DataFrame:
        """从Binance合约下载历史K线数据并保存为CSV"""
        exchange = ccxt.binance(ConstructorArgs(enableRateLimit=True, options={"defaultType": "future"}))

        all_ohlcv = []
        since = start

        while since < end:
            try:
                ohlcv = exchange.fetch_ohlcv(symbol.ccxt(), interval, since=since, limit=1000)
                if not ohlcv:
                    logger.info("No more data available")
                    break
                filtered_ohlcv = [row for row in ohlcv if row[0] <= end]
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

        df = pd.DataFrame(all_ohlcv, columns=self.columns)
        df['timestamp'] = df['timestamp'].astype(int)
        df = df.sort_values('timestamp').reset_index(drop=True)

        return df
