# Backtest Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the backtest module so that `ExClient.fetch_ohlcv` supports time-range parameters, `HistoricalDataLoader` becomes `KlineDataStore`, and `BacktestAnalysis` decouples analysis from `BacktestRunner`.

**Architecture:** Extend `fetch_ohlcv` with optional `start_time/end_time` params across the `ExClient` hierarchy. Live clients use `KlineDataStore` cache or ccxt fallback; `BacktestClient` filters in-memory data. New `BacktestAnalysis` class wraps `BacktestAnalyzer` as a facade reading from `BacktestClient` state.

**Tech Stack:** Python 3.12+, pytest, ccxt

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Rename + modify | `backtest/data_loader.py` → `backtest/kline_data_store.py` | K-line data fetch + cache (remove unused methods) |
| Modify | `client/ex_client.py` | Add `data_store` attr, extend `fetch_ohlcv` signature |
| Modify | `client/binance_client.py` | Add `data_store` param, implement time-aware `fetch_ohlcv` |
| Modify | `client/bybit_client.py` | Update `fetch_ohlcv` signature (no-op params) |
| Modify | `client/okx_client.py` | Update `fetch_ohlcv` signature (no-op params) |
| Modify | `client/mexc_client.py` | Update `fetch_ohlcv` signature (no-op params) |
| Modify | `backtest/backtest_client.py` | Update `fetch_ohlcv` with time params |
| Modify | `backtest/runner.py` | Use `KlineDataStore` and `BacktestAnalysis` |
| Create | `backtest/backtest_analysis.py` | `BacktestAnalysis` facade class |
| Modify | `run_backtest.py` | Update imports, replace `filter_by_date_range` |
| Modify | `run_alpha_trend_backtest.py` | Update imports |
| Modify | `backtest_signal_grid.py` | Update imports |
| Modify | `scripts/download_eth_data.py` | Update imports |
| Modify | `test/test_data_loader.py` | Update imports, class name |
| Modify | `test/test_backtest_rewrite.py` | Add tests for time-param `fetch_ohlcv` |
| Modify | `test/test_backtest_event_loop.py` | Update `MockExClient.fetch_ohlcv` signature |
| Modify | `test/test_general_strategy.py` | Update `MockExClient.fetch_ohlcv` signature |
| Modify | `test/test_event_layer.py` | Update `MockExClient.fetch_ohlcv` signature |
| Create | `test/test_backtest_analysis.py` | Tests for `BacktestAnalysis` |

---

### Task 1: Rename HistoricalDataLoader → KlineDataStore and remove unused methods

**Files:**
- Rename: `backtest/data_loader.py` → `backtest/kline_data_store.py`
- Modify: `backtest/kline_data_store.py`

- [ ] **Step 1: Write the failing test**

Update `test/test_data_loader.py` with new imports and class name, plus test that removed methods are gone:

```python
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from persistence.kline_data_store import KlineDataStore
from model import Symbol


class TestEnsureData:
    @pytest.fixture
    def store(self):
        return KlineDataStore()

    @pytest.fixture
    def symbol(self):
        return Symbol(base="eth", quote="usdt")

    def test_file_naming_without_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        expected_path = "data/ETHUSDT_5m_20260301_0s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = store.ensure_data(symbol, "5m", start_time, end_time, "data")

        assert result == expected_path

    def test_file_naming_with_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_path = "data/ETHUSDT_5m_20260301_2592000s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = store.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        assert result == expected_path

    def test_download_start_time_with_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_download_start = datetime(2026, 1, 30, tzinfo=timezone.utc)

        with patch.object(Path, 'exists', return_value=False):
            with patch.object(store, 'download_and_save_historical_data') as mock_download:
                mock_download.return_value = "mock_path"
                store.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        mock_download.assert_called_once()
        call_args = mock_download.call_args
        assert call_args[0][2] == expected_download_start


class TestRemovedMethods:
    @pytest.fixture
    def store(self):
        return KlineDataStore()

    def test_no_load_json(self, store):
        assert not hasattr(store, 'load_json')

    def test_no_load_from_dataframe(self, store):
        assert not hasattr(store, 'load_from_dataframe')

    def test_no_filter_by_date_range(self, store):
        assert not hasattr(store, 'filter_by_date_range')

    def test_no_get_price_series(self, store):
        assert not hasattr(store, 'get_price_series')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_data_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.kline_data_store'`

- [ ] **Step 3: Create new file and remove old file**

Create `backtest/kline_data_store.py` with the content below, then delete `backtest/data_loader.py`:

```python
import pandas as pd
import json
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
    """K线数据缓存与获取"""

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
        return self.download_and_save_historical_data(symbol, timeframe, download_start, end_dt, file_path)

    def download_and_save_historical_data(
        self,
        symbol: Symbol,
        interval: str,
        start_time: Union[str, datetime],
        end_time: Union[str, datetime],
        file_path: str
    ) -> str:
        """从Binance合约下载历史K线数据并保存为CSV"""
        exchange = ccxt.binance(ConstructorArgs(options={"defaultType": "future"}))

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
```

Then delete the old file:
```bash
rm backtest/data_loader.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_data_loader.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/kline_data_store.py backtest/data_loader.py test/test_data_loader.py
git commit -m "refactor: rename HistoricalDataLoader to KlineDataStore, remove unused methods"
```

---

### Task 2: Update all imports referencing HistoricalDataLoader / data_loader

**Files:**
- Modify: `backtest/runner.py`
- Modify: `run_backtest.py`
- Modify: `run_alpha_trend_backtest.py`
- Modify: `backtest_signal_grid.py`
- Modify: `scripts/download_eth_data.py`

- [ ] **Step 1: Update imports in backtest/runner.py**

In `backtest/runner.py`, line 11:

```python
# Before:
from backtest.data_loader import HistoricalDataLoader
# After:
from persistence.kline_data_store import KlineDataStore
```

Line 31:
```python
# Before:
data_loader = HistoricalDataLoader()
# After:
data_loader = KlineDataStore()
```

Line 79 type hint:
```python
# Before:
def _load_data(self, data_loader: HistoricalDataLoader,
# After:
def _load_data(self, data_loader: KlineDataStore,
```

- [ ] **Step 2: Update imports in run_backtest.py**

In `run_backtest.py`, line 12:

```python
# Before:
from backtest.data_loader import HistoricalDataLoader
# After:
from persistence.kline_data_store import KlineDataStore
```

Line 40:
```python
# Before:
data_loader = HistoricalDataLoader()
# After:
data_loader = KlineDataStore()
```

Line 62 — `filter_by_date_range` was removed, replace with inline filter:
```python
# Before:
klines = data_loader.filter_by_date_range(klines, start_timestamp, end_timestamp)
# After:
klines = [k for k in klines
          if (start_timestamp is None or k.timestamp >= start_timestamp)
          and (end_timestamp is None or k.timestamp <= end_timestamp)]
```

- [ ] **Step 3: Update imports in run_alpha_trend_backtest.py**

In `run_alpha_trend_backtest.py`, line 8:

```python
# Before:
from backtest.data_loader import HistoricalDataLoader
# After:
from persistence.kline_data_store import KlineDataStore
```

Line 63:
```python
# Before:
data_loader = HistoricalDataLoader()
# After:
data_loader = KlineDataStore()
```

- [ ] **Step 4: Update imports in backtest_signal_grid.py**

In `backtest_signal_grid.py`, line 15:

```python
# Before:
from backtest.data_loader import HistoricalDataLoader
# After:
from persistence.kline_data_store import KlineDataStore
```

Line 46:
```python
# Before:
data_loader = HistoricalDataLoader()
# After:
data_loader = KlineDataStore()
```

- [ ] **Step 5: Update imports in scripts/download_eth_data.py**

In `scripts/download_eth_data.py`, line 15:

```python
# Before:
from backtest.data_loader import HistoricalDataLoader
# After:
from persistence.kline_data_store import KlineDataStore
```

Line 31:
```python
# Before:
data_loader = HistoricalDataLoader()
# After:
data_loader = KlineDataStore()
```

- [ ] **Step 6: Run existing tests to verify nothing is broken**

Run: `uv run pytest test/test_data_loader.py test/test_backtest_rewrite.py test/test_backtest_event_loop.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backtest/runner.py run_backtest.py run_alpha_trend_backtest.py backtest_signal_grid.py scripts/download_eth_data.py
git commit -m "refactor: update all HistoricalDataLoader imports to KlineDataStore"
```

---

### Task 3: Extend ExClient.fetch_ohlcv signature and add data_store attribute

**Files:**
- Modify: `client/ex_client.py`

- [ ] **Step 1: Write the failing test**

In `test/test_backtest_rewrite.py`, add a new test class at the end:

```python
class TestFetchOhlcvTimeParams:
    def test_fetch_ohlcv_with_start_time(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)

        start_ts = TS_BASE + 3 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', start_time=start_ts)
        assert len(result) == 7
        assert result[0].timestamp == start_ts

    def test_fetch_ohlcv_with_end_time(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)

        end_ts = TS_BASE + 5 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', end_time=end_ts)
        assert len(result) == 6
        assert result[-1].timestamp == end_ts

    def test_fetch_ohlcv_with_time_range(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)

        start_ts = TS_BASE + 2 * 60_000
        end_ts = TS_BASE + 7 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', start_time=start_ts, end_time=end_ts)
        assert len(result) == 6
        assert result[0].timestamp == start_ts
        assert result[-1].timestamp == end_ts

    def test_fetch_ohlcv_time_range_with_limit(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)

        start_ts = TS_BASE + 2 * 60_000
        end_ts = TS_BASE + 7 * 60_000
        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3, start_time=start_ts, end_time=end_ts)
        assert len(result) == 3
        assert result[0].timestamp == TS_BASE + 5 * 60_000

    def test_fetch_ohlcv_no_time_params_unchanged(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(10)
        client.load_historical_data(SYMBOL, '1m', klines)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3)
        assert len(result) == 3
        assert result[-1].timestamp == TS_BASE + 5 * 60_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_backtest_rewrite.py::TestFetchOhlcvTimeParams -v`
Expected: FAIL — `TypeError: fetch_ohlcv() got an unexpected keyword argument 'start_time'`

- [ ] **Step 3: Update ExClient base class signature**

In `client/ex_client.py`, update `ExClient.fetch_ohlcv` (lines 49-75):

```python
class ExClient(ABC):
    exchange_name: str
    exchange: Exchange
    order_repo: OrderRepository
    data_store: Any | None = None

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        raise NotImplementedError()

    @abstractmethod
    def balance(self, coin: str) -> float:
        pass

    @abstractmethod
    def cancel(self, custom_id: str, symbol: Symbol) -> Order | None:
        pass

    @abstractmethod
    def query_order(self, custom_id: str, symbol: Symbol) -> Order | None:
        pass

    def place_order_v2(
        self,
        strategy_id: str,
        custom_id: str,
        symbol: Symbol,
        order_side: OrderSide,
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Optional[Order]:
        pass

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        if limit < 1:
            return []

        list_ohlcv = self.exchange.fetch_ohlcv(symbol.ccxt(), timeframe, limit=limit)
        klines: list[Kline] = []
        for ohlcv in list_ohlcv:
            klines.append(
                Kline(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ohlcv[0],
                    open=ohlcv[1],
                    high=ohlcv[2],
                    low=ohlcv[3],
                    close=ohlcv[4],
                    volume=ohlcv[5],
                    finished=True
                )
            )

        if klines:
            timeframe_ms = self.exchange.parse_timeframe(timeframe)
            klines[-1].finished = klines[-1].timestamp + timeframe_ms * 1000 <= int(time.time() * 1000)

        return klines
```

Add `Any` to the existing imports at top of file:
```python
from typing import List, Dict, Any, Optional
```

Note: `Any` is already imported via `Dict` and `List` usage — verify it's present.

- [ ] **Step 4: Update BacktestClient.fetch_ohlcv**

In `backtest/backtest_client.py`, replace the `fetch_ohlcv` method (lines 282-296):

```python
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        key = f"{symbol.binance()}_{timeframe}"
        if key not in self.historical_data:
            logger.warning("No historical data available for %s timeframe %s", symbol.binance(), timeframe)
            return []

        klines = self.historical_data[key]

        if start_time is not None or end_time is not None:
            klines = [k for k in klines
                      if (start_time is None or k.timestamp >= start_time)
                      and (end_time is None or k.timestamp <= end_time)]
            return klines[-limit:] if len(klines) > limit else klines

        current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]
        if not current_klines:
            logger.warning("No klines available before timestamp %d for timeframe %s",
                          self.current_timestamp, timeframe)
            return []

        return current_klines[-limit:] if len(current_klines) >= limit else current_klines
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest test/test_backtest_rewrite.py -v`
Expected: ALL PASS (including new time-param tests and existing ones)

- [ ] **Step 6: Commit**

```bash
git add client/ex_client.py backtest/backtest_client.py test/test_backtest_rewrite.py
git commit -m "feat: extend fetch_ohlcv with start_time/end_time params"
```

---

### Task 4: Update MockExClient.fetch_ohlcv signatures in test files

**Files:**
- Modify: `test/test_backtest_event_loop.py`
- Modify: `test/test_general_strategy.py`
- Modify: `test/test_event_layer.py`

- [ ] **Step 1: Update test/test_backtest_event_loop.py line 18**

```python
# Before:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int) -> list[Kline]:
# After:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
```

- [ ] **Step 2: Update test/test_general_strategy.py line 11**

```python
# Before:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int) -> List[Kline]:
# After:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> List[Kline]:
```

- [ ] **Step 3: Update test/test_event_layer.py line 138**

```python
# Before:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int) -> list[Kline]:
# After:
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
```

- [ ] **Step 4: Run all tests to verify**

Run: `uv run pytest test/test_backtest_event_loop.py test/test_general_strategy.py test/test_event_layer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add test/test_backtest_event_loop.py test/test_general_strategy.py test/test_event_layer.py
git commit -m "refactor: update MockExClient.fetch_ohlcv signatures in tests"
```

---

### Task 5: Implement BinanceSwapClient time-aware fetch_ohlcv

**Files:**
- Modify: `client/binance_client.py`

- [ ] **Step 1: Update BinanceSwapClient.__init__ to accept data_store**

In `client/binance_client.py`, update `__init__` (line 19):

```python
class BinanceSwapClient(ExSwapClient):
    def __init__(self, api_key: str, api_secret: str, is_test: bool = False,
                 order_repo: OrderRepository | None = None,
                 data_store: Any | None = None):
        self.exchange_name = 'binance'
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store

        self.exchange = ccxt.binance(ConstructorArgs(
            apiKey=api_key,
            secret=api_secret,
            options={
                "defaultType": "future",
            }
        ))
        self.exchange.enable_demo_trading(is_test)
        self.exchange.load_markets()
        self.exchange_info: Dict[str, Any] = {}
```

- [ ] **Step 2: Override fetch_ohlcv with time-aware logic**

Add `fetch_ohlcv` method and helper to `BinanceSwapClient`, after `symbol_info` method:

```python
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        if start_time is not None or end_time is not None:
            if self.data_store:
                from datetime import datetime, timezone as tz
                start_dt = datetime.fromtimestamp(start_time / 1000, tz=tz.utc) if start_time else None
                end_dt = datetime.fromtimestamp(end_time / 1000, tz=tz.utc) if end_time else None

                start_str = start_dt.isoformat() if start_dt else ""
                end_str = end_dt.isoformat() if end_dt else ""

                file_path = self.data_store.ensure_data(
                    symbol, timeframe,
                    start_str or "2020-01-01",
                    end_str or datetime.now(tz=tz.utc).isoformat(),
                    "data",
                )
                klines = self.data_store.load_csv(file_path, symbol, timeframe)
                return self._filter_klines_by_time(klines, start_time, end_time, limit)
            return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit, start_time, end_time)
        return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit)

    def _fetch_ohlcv_via_ccxt(self, symbol: Symbol, timeframe: str, limit: int = 100,
                               start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        since = start_time
        list_ohlcv = self.exchange.fetch_ohlcv(
            symbol.ccxt(), timeframe, since=since, limit=limit
        )
        klines: list[Kline] = []
        for ohlcv in list_ohlcv:
            ts = ohlcv[0]
            if end_time is not None and ts > end_time:
                break
            klines.append(
                Kline(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=ohlcv[1],
                    high=ohlcv[2],
                    low=ohlcv[3],
                    close=ohlcv[4],
                    volume=ohlcv[5],
                    finished=True
                )
            )

        if klines:
            timeframe_ms = self.exchange.parse_timeframe(timeframe)
            klines[-1].finished = klines[-1].timestamp + timeframe_ms * 1000 <= int(time.time() * 1000)

        return klines[-limit:] if len(klines) > limit else klines

    @staticmethod
    def _filter_klines_by_time(klines: list[Kline], start_time: int | None,
                                end_time: int | None, limit: int) -> list[Kline]:
        filtered = [
            k for k in klines
            if (start_time is None or k.timestamp >= start_time)
            and (end_time is None or k.timestamp <= end_time)
        ]
        return filtered[-limit:] if len(filtered) > limit else filtered
```

Add `Kline` to the imports at the top of `client/binance_client.py`:
```python
from model import Order, PositionSide, Symbol, PlaceOrderBehavior, SymbolInfo
from model import OrderSide, OrderStatus, Kline
```

- [ ] **Step 3: Run existing tests to verify nothing is broken**

Run: `uv run pytest test/ -v --ignore=test/test_ccxt.py`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add client/binance_client.py
git commit -m "feat: BinanceSwapClient time-aware fetch_ohlcv with KlineDataStore cache"
```

---

### Task 6: Update other exchange client signatures

**Files:**
- Modify: `client/bybit_client.py`
- Modify: `client/okx_client.py`
- Modify: `client/mexc_client.py`

- [ ] **Step 1: Update BybitSwapClient — add data_store to __init__**

In `client/bybit_client.py`, `BybitSwapClient.__init__` (line 12):

```python
class BybitSwapClient(ExSwapClient):
    def __init__(self, _api_key, _api_secret, test, order_repo: OrderRepository | None = None,
                 data_store: Any | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store
        self.client = ccxt.bybit({
            'apiKey': _api_key,
            'secret': _api_secret,
        })
        if test:
            self.client.enable_demo_trading(test)
```

- [ ] **Step 2: Update OkxSwapClient — add data_store to __init__**

In `client/okx_client.py`, `OkxSwapClient.__init__` (line 31):

```python
class OkxSwapClient(ExSwapClient):
    def __init__(self, api_key, secret, password, test: bool = False,
                 order_repo: OrderRepository | None = None,
                 data_store: Any | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store
        self.exchange = ccxt.okx({
```

- [ ] **Step 3: Update MexcSwapClient — add data_store to __init__**

In `client/mexc_client.py`, `MexcSwapClient.__init__` (line 10):

```python
class MexcSwapClient(ExSwapClient):
    def __init__(self, order_repo: OrderRepository | None = None,
                 data_store: Any | None = None):
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store
```

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest test/ -v --ignore=test/test_ccxt.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add client/bybit_client.py client/okx_client.py client/mexc_client.py
git commit -m "refactor: add data_store attribute to Bybit/OKX/MEXC clients"
```

---

### Task 7: Create BacktestAnalysis class

**Files:**
- Create: `backtest/backtest_analysis.py`
- Create: `test/test_backtest_analysis.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_backtest_analysis.py`:

```python
import pytest
from model import Symbol, Kline, OrderSide, PositionSide, OrderStatus
from backtest.backtest_client import BacktestClient
from backtest.backtest_analysis import BacktestAnalysis
from persistence.order_repository import InMemoryOrderRepository


SYMBOL = Symbol(base='eth', quote='usdt')
TS_BASE = 1_700_000_000_000


def _client_with_trade() -> BacktestClient:
    repo = InMemoryOrderRepository()
    client = BacktestClient(order_repo=repo, initial_balance=10_000.0)
    client.update_current_timestamp(TS_BASE)
    client.current_prices[SYMBOL.binance()] = 2000.0

    client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                          position_side=PositionSide.LONG)
    client.update_current_timestamp(TS_BASE + 3600_000)
    client.current_prices[SYMBOL.binance()] = 2200.0
    client.place_order_v2('test', 'exit_entry', SYMBOL, OrderSide.SELL, 1.0,
                          position_side=PositionSide.LONG)
    return client


class TestBacktestAnalysis:
    def test_analyze_returns_dict(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert isinstance(result, dict)
        assert 'summary' in result
        assert 'risk_metrics' in result
        assert 'trade_metrics' in result
        assert 'final_state' in result

    def test_final_state_includes_balance(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert 'balance' in result['final_state']
        assert 'pnl' in result['final_state']

    def test_report_returns_string(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        report = analysis.report()
        assert isinstance(report, str)
        assert "BACKTEST REPORT" in report

    def test_empty_backtest(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert result['summary']['total_trades'] == 0
        assert result['final_state']['balance'] == 10_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_backtest_analysis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.backtest_analysis'`

- [ ] **Step 3: Create BacktestAnalysis class**

Create `backtest/backtest_analysis.py`:

```python
from typing import Any

from backtest.backtest_analyzer import BacktestAnalyzer
from backtest.backtest_client import BacktestClient


class BacktestAnalysis:
    """从 BacktestClient 状态数据生成回测分析"""

    def __init__(self, client: BacktestClient, initial_balance: float) -> None:
        self.client = client
        self.initial_balance = initial_balance
        self._analyzer = BacktestAnalyzer(initial_balance)

    def analyze(self) -> dict[str, Any]:
        trade_history = self.client.get_trade_history()
        positions = self.client.positions()
        final_balance = self.client.get_final_balance()

        analysis = self._analyzer.analyze(trade_history)
        analysis['final_state'] = {
            'balance': final_balance,
            'open_positions': positions,
            'pnl': final_balance - self.initial_balance,
        }
        return analysis

    def report(self) -> str:
        analysis = self.analyze()
        return self._analyzer.generate_report(analysis)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_backtest_analysis.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_analysis.py test/test_backtest_analysis.py
git commit -m "feat: add BacktestAnalysis facade class"
```

---

### Task 8: Update BacktestRunner to use KlineDataStore and BacktestAnalysis

**Files:**
- Modify: `backtest/runner.py`

- [ ] **Step 1: Update imports in backtest/runner.py**

The import was already updated in Task 2. Now update the `BacktestAnalyzer` import:

```python
# Before:
from backtest.backtest_analyzer import BacktestAnalyzer
# After:
from backtest.backtest_analysis import BacktestAnalysis
```

- [ ] **Step 2: Update BacktestRunner.run() method**

In `backtest/runner.py`, replace the `run()` method (lines 30-77):

```python
    def run(self) -> BacktestResult:
        data_loader = KlineDataStore()
        client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=self.config.initial_balance,
            maker_fee=self.config.maker_fee,
            taker_fee=self.config.taker_fee,
        )

        klines = self._load_data(data_loader, client)
        if not klines:
            logger.warning("No historical data loaded, returning empty result")
            return self._empty_result()

        klines.sort(key=lambda k: k.timestamp)

        strategy = self._create_strategy(client)
        self._preinitialize_klines(strategy, client, klines)
        handler = KlineHandler(strategy)

        start_ts = self._parse_timestamp(self.config.start_date)
        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_timestamp=start_ts,
        )
        event_loop.set_backtest_client(client)
        event_loop.add_handler(handler)

        logger.info("Starting backtest...")
        event_loop.start()
        event_loop.stop()

        backtest_analysis = BacktestAnalysis(client, self.config.initial_balance)
        analysis = backtest_analysis.analyze()
        report = backtest_analysis.report()

        final_balance = client.get_final_balance()
        trade_history = client.get_trade_history()

        logger.info("Backtest completed. Trades: %d, Final balance: %.2f",
                     len(trade_history), final_balance)

        return BacktestResult(
            analysis=analysis,
            trade_history=trade_history,
            final_balance=final_balance,
            report=report,
        )
```

- [ ] **Step 3: Update _empty_result method**

In `backtest/runner.py`, replace `_empty_result` (lines 162-170):

```python
    def _empty_result(self) -> BacktestResult:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=self.config.initial_balance)
        backtest_analysis = BacktestAnalysis(client, self.config.initial_balance)
        analysis = backtest_analysis.analyze()
        return BacktestResult(
            analysis=analysis,
            trade_history=[],
            final_balance=self.config.initial_balance,
            report=backtest_analysis.report(),
        )
```

- [ ] **Step 4: Remove unused BacktestAnalyzer import**

The `BacktestAnalyzer` import was replaced in Step 1. Verify no remaining references:

```bash
grep -n "BacktestAnalyzer" backtest/runner.py
```

Expected: No results

- [ ] **Step 5: Run tests to verify**

Run: `uv run pytest test/ -v --ignore=test/test_ccxt.py`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backtest/runner.py
git commit -m "refactor: BacktestRunner uses BacktestAnalysis facade"
```

---

### Task 9: Full integration test — run all tests

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest test/ -v --ignore=test/test_ccxt.py`
Expected: ALL PASS

- [ ] **Step 2: Run mypy type check**

Run: `uv run mypy client/ex_client.py client/binance_client.py backtest/backtest_client.py backtest/backtest_analysis.py backtest/runner.py backtest/kline_data_store.py --ignore-missing-imports`
Expected: No errors

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address type check issues from backtest refactoring"
```
