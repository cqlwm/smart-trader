import pandas as pd
import json
from typing import List, Dict, Any, Optional, Union
from pathlib import Path
from datetime import datetime
import time
import ccxt
import log

from model import Kline, Symbol
from ccxt.base.types import ConstructorArgs
logger = log.getLogger(__name__)


class HistoricalDataLoader:
    """历史数据加载器"""

    def __init__(self):
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def load_csv(self, file_path: str, symbol: Symbol, timeframe: str) -> List[Kline]:
        """
        从CSV文件加载历史K线数据
        CSV格式: timestamp,open,high,low,close,volume
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")

        cache_key = f"{file_path}_{symbol.binance()}_{timeframe}"
        if cache_key in self.data_cache:
            df = self.data_cache[cache_key]
        else:
            # 读取CSV文件
            df = pd.read_csv(file_path)
            # 确保列名正确
            expected_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in expected_columns):
                raise ValueError(f"CSV file must contain columns: {expected_columns}")

            # 转换数据类型
            df['timestamp'] = df['timestamp'].astype(int)
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)

            # 缓存数据
            self.data_cache[cache_key] = df

        # 转换为Kline对象列表
        klines = []
        for _, row in df.iterrows():
            kline = Kline(
                symbol=symbol,
                timeframe=timeframe,
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                timestamp=int(row['timestamp']),
                finished=True
            )
            klines.append(kline)

        logger.info(f"Loaded {len(klines)} klines from {file_path}")
        return klines

    def load_json(self, file_path: str, symbol: Symbol, timeframe: str) -> List[Kline]:
        """
        从JSON文件加载历史K线数据
        JSON格式: [{"timestamp": 1234567890, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}, ...]
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")

        cache_key = f"{file_path}_{symbol.binance()}_{timeframe}"
        if cache_key in self.data_cache:
            df = self.data_cache[cache_key]
        else:
            # 读取JSON文件
            with open(file_path, 'r') as f:
                data = json.load(f)

            # 转换为DataFrame
            df = pd.DataFrame(data)
            # 确保列存在
            expected_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in expected_columns):
                raise ValueError(f"JSON data must contain keys: {expected_columns}")

            # 转换数据类型
            df['timestamp'] = df['timestamp'].astype(int)
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)

            # 缓存数据
            self.data_cache[cache_key] = df

        # 转换为Kline对象列表
        klines = []
        for _, row in df.iterrows():
            kline = Kline(
                symbol=symbol,
                timeframe=timeframe,
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                timestamp=int(row['timestamp']),
                finished=True
            )
            klines.append(kline)

        logger.info(f"Loaded {len(klines)} klines from {file_path}")
        return klines

    def load_from_dataframe(self, df: pd.DataFrame, symbol: Symbol, timeframe: str) -> List[Kline]:
        """
        从pandas DataFrame加载历史K线数据
        DataFrame必须包含: timestamp, open, high, low, close, volume 列
        """
        expected_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in expected_columns):
            raise ValueError(f"DataFrame must contain columns: {expected_columns}")

        # 转换为Kline对象列表
        klines = []
        for _, row in df.iterrows():
            kline = Kline(
                symbol=symbol,
                timeframe=timeframe,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume']),
                timestamp=int(row['timestamp']),
                finished=True
            )
            klines.append(kline)

        logger.info(f"Loaded {len(klines)} klines from DataFrame")
        return klines

    def filter_by_date_range(self, klines: List[Kline], start_timestamp: Optional[int] = None,
                           end_timestamp: Optional[int] = None) -> List[Kline]:
        """
        按时间范围过滤K线数据
        """
        if start_timestamp is None and end_timestamp is None:
            return klines

        filtered_klines = []
        for kline in klines:
            if start_timestamp and kline.timestamp < start_timestamp:
                continue
            if end_timestamp and kline.timestamp > end_timestamp:
                continue
            filtered_klines.append(kline)

        logger.info(f"Filtered klines from {len(klines)} to {len(filtered_klines)}")
        return filtered_klines

    def get_price_series(self, klines: List[Kline]) -> pd.Series:
        """
        获取收盘价序列，用于技术分析
        """
        prices = [kline.close for kline in klines]
        timestamps = [kline.timestamp for kline in klines]
        return pd.Series(prices, index=timestamps, name='close')

    def clear_cache(self):
        """清除数据缓存"""
        self.data_cache.clear()
        logger.info("Data cache cleared")

    def download_and_save_historical_data(
        self,
        symbol: Symbol,
        interval: str,
        start_time: Union[str, datetime],
        end_time: Union[str, datetime],
        file_path: str
    ) -> str:
        """
        从Binance合约下载历史K线数据并保存为CSV

        Args:
            symbol: 交易对 (如 Symbol(base='BTC', quote='USDT'))
            interval: 时间周期 (如 '1m', '1h', '1d')
            start_time: 开始时间 (字符串格式如 '2023-01-01' 或 datetime对象)
            end_time: 结束时间 (字符串格式如 '2023-12-31' 或 datetime对象)
            file_path: 保存文件路径

        Returns:
            保存的文件路径
        """
        # 初始化Binance期货客户端
        exchange = ccxt.binance(ConstructorArgs(
            options={
                "defaultType": "future",
            }
        ))
        

        # 处理时间参数
        if isinstance(start_time, str):
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        else:
            start_dt = start_time

        if isinstance(end_time, str):
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        else:
            end_dt = end_time

        start_timestamp = int(start_dt.timestamp() * 1000)
        end_timestamp = int(end_dt.timestamp() * 1000)

        logger.info(f"Downloading {symbol.binance()} {interval} data from {start_dt} to {end_dt}")

        all_ohlcv = []
        since = start_timestamp

        # 分页获取数据
        while since < end_timestamp:
            try:
                # 获取数据，每次最多1000条
                ohlcv = exchange.fetch_ohlcv(symbol.ccxt(), interval, since=since, limit=1000)

                if not ohlcv:
                    logger.info("No more data available")
                    break

                # 过滤超出结束时间的数据
                filtered_ohlcv = [row for row in ohlcv if row[0] <= end_timestamp]
                all_ohlcv.extend(filtered_ohlcv)

                # 如果获取的数据少于1000条，说明已经到最新数据
                if len(ohlcv) < 1000:
                    break

                # 更新since为最后一条数据的timestamp
                since = ohlcv[-1][0] + 1  # +1毫秒避免重复

                logger.info(f"Fetched {len(filtered_ohlcv)} klines, total: {len(all_ohlcv)}")

                # 添加延迟避免被限流
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error fetching data: {e}")
                break

        if not all_ohlcv:
            raise ValueError("No data downloaded")

        # 转换为DataFrame
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # 确保数据类型正确
        df['timestamp'] = df['timestamp'].astype(int)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # 按时间戳排序
        df = df.sort_values('timestamp').reset_index(drop=True)

        # 创建目录（如果不存在）
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)

        # 保存为CSV
        df.to_csv(file_path, index=False)

        logger.info(f"Saved {len(df)} klines to {file_path}")

        return file_path
