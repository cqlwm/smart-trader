# Backtest Refactoring Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BacktestClient internalizes KlineDataStore for self-loading data, delete BacktestRunner, rename BacktestAnalysis → TradeAnalysis with generic ExSwapClient interface.

**Architecture:** BacktestClient receives KlineDataStore + data config at construction, auto-loads all K-line data into an internal `_kline_cache`, and `fetch_ohlcv` reads from cache filtered by `current_timestamp`. TradeAnalysis accepts any `ExSwapClient` and reads from its `get_trade_history()` / `positions()` / `balance()` methods. BacktestRunner is deleted; its orchestration logic moves to callers or gets inlined.

**Tech Stack:** Python 3.12+, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `backtest/backtest_client.py` | Add KlineDataStore, auto-load data, replace `historical_data` with `_kline_cache`, remove `load_historical_data` |
| Rename + modify | `backtest/backtest_analysis.py` → `backtest/trade_analysis.py` | Rename class to `TradeAnalysis`, accept `ExSwapClient`, derive `initial_balance` from `client.balance('USDT')` |
| Delete | `backtest/runner.py` | Orchestration no longer needed |
| Modify | `backtest/config.py` | Add `data_dir` and `extra_timeframes_offset` fields |
| Modify | `backtest/backtest_event_loop.py` | Add `all_klines` property for caller access |
| Modify | `api/routes/backtest.py` | Inline BacktestRunner logic directly |
| Modify | `test/test_backtest_rewrite.py` | Update tests: replace `load_historical_data`/`historical_data` with new API |
| Modify | `test/test_backtest_analysis.py` → `test/test_trade_analysis.py` | Rename, update to use `TradeAnalysis` |
| Modify | `run_backtest.py` | Update to use new BacktestClient constructor |
| Modify | `run_alpha_trend_backtest.py` | Update to use new BacktestClient constructor |
| Modify | `backtest_signal_grid.py` | Update to use new BacktestClient constructor |

---

### Task 1: Refactor BacktestClient to internalize KlineDataStore

**Files:**
- Modify: `backtest/backtest_client.py`
- Modify: `backtest/config.py`

- [ ] **Step 1: Write the failing test**

Add new test class to `test/test_backtest_rewrite.py`:

```python
from datetime import timedelta
from unittest.mock import MagicMock
from backtest.kline_data_store import KlineDataStore


class TestBacktestClientAutoLoad:
    def test_auto_load_data_from_store(self) -> None:
        """BacktestClient should auto-load data from KlineDataStore on construction."""
        klines = _make_klines(10)
        store = KlineDataStore()
        store.data_cache["data/ETHUSDT_1m_20250101_0s_20250201.csv"] = pd.DataFrame()

        with patch.object(store, 'ensure_data', return_value="data/ETHUSDT_1m_20250101_0s_20250201.csv"):
            with patch.object(store, 'load_csv', return_value=klines):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                )

        # fetch_ohlcv without time params uses current_timestamp
        client.update_current_timestamp(TS_BASE + 5 * 60_000)
        result = client.fetch_ohlcv(SYMBOL, '1m', limit=3)
        assert len(result) == 3
        assert result[-1].timestamp == TS_BASE + 5 * 60_000

    def test_auto_load_with_extra_timeframes(self) -> None:
        """BacktestClient should load extra_timeframes data too."""
        klines_1m = _make_klines(10)
        klines_1d = _make_klines(5)

        store = KlineDataStore()
        with patch.object(store, 'ensure_data', return_value="data/mock.csv"):
            with patch.object(store, 'load_csv', side_effect=[klines_1m, klines_1d]):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                    extra_timeframes=('1d',),
                )

        client.update_current_timestamp(TS_BASE + 3 * 60_000)
        result_1d = client.fetch_ohlcv(SYMBOL, '1d', limit=3)
        assert len(result_1d) == 3

    def test_no_data_store_falls_back_to_empty(self) -> None:
        """BacktestClient without data_store works but fetch_ohlcv returns empty."""
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        result = client.fetch_ohlcv(SYMBOL, '1m')
        assert result == []

    def test_get_all_klines(self) -> None:
        """BacktestClient should expose all klines for BacktestEventLoop."""
        klines = _make_klines(10)
        store = KlineDataStore()
        with patch.object(store, 'ensure_data', return_value="data/mock.csv"):
            with patch.object(store, 'load_csv', return_value=klines):
                client = BacktestClient(
                    order_repo=InMemoryOrderRepository(),
                    data_store=store,
                    symbol=SYMBOL,
                    timeframe='1m',
                    start_date='2025-01-01',
                    end_date='2025-02-01',
                )

        all_klines = client.get_all_klines()
        assert len(all_klines) == 10
        assert all_klines[0].timestamp == TS_BASE
```

Also add `from unittest.mock import patch` and `import pandas as pd` at the top of the test file if not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_backtest_rewrite.py::TestBacktestClientAutoLoad -v`
Expected: FAIL — `TypeError: __init__() got unexpected keyword arguments`

- [ ] **Step 3: Update BacktestClient.__init__ and replace historical_data with _kline_cache**

Replace the `BacktestClient.__init__` in `backtest/backtest_client.py` with:

```python
    def __init__(
        self,
        order_repo: OrderRepository,
        initial_balance: float = 10000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        symbol_infos: dict[str, SymbolInfo] | None = None,
        data_store: KlineDataStore | None = None,
        symbol: Symbol | None = None,
        timeframe: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        extra_timeframes: tuple[str, ...] = (),
        data_dir: str = "data",
    ) -> None:
        self.exchange_name = 'backtest'
        self.exchange = None  # type: ignore
        self.order_repo = order_repo

        self._balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        self._symbol_infos: dict[str, SymbolInfo] = symbol_infos or {}

        self._positions: dict[str, _Position] = {}

        self.current_prices: dict[str, float] = {}

        self._kline_cache: dict[str, list[Kline]] = {}
        self.current_timestamp: int = 0

        if data_store and symbol and timeframe and start_date and end_date:
            self._load_data_from_store(data_store, symbol, timeframe, start_date, end_date, extra_timeframes, data_dir)

        logger.info("BacktestClient initialized with balance: %s", initial_balance)
```

Add the `_load_data_from_store` and `get_all_klines` methods:

```python
    def _load_data_from_store(
        self,
        data_store: KlineDataStore,
        symbol: Symbol,
        timeframe: str,
        start_date: str,
        end_date: str,
        extra_timeframes: tuple[str, ...] = (),
        data_dir: str = "data",
    ) -> None:
        file_path = data_store.ensure_data(symbol, timeframe, start_date, end_date, data_dir)
        klines = data_store.load_csv(file_path, symbol, timeframe)
        if klines:
            self._store_klines(symbol, timeframe, klines)
            logger.info("Auto-loaded %d klines for %s %s", len(klines), symbol.binance(), timeframe)

        for tf in extra_timeframes:
            tf_offset = self._extra_tf_offset(tf)
            tf_path = data_store.ensure_data(symbol, tf, start_date, end_date, data_dir, offset=tf_offset)
            tf_klines = data_store.load_csv(tf_path, symbol, tf)
            if tf_klines:
                self._store_klines(symbol, tf, tf_klines)
                logger.info("Auto-loaded %d klines for %s %s", len(tf_klines), symbol.binance(), tf)

    def _store_klines(self, symbol: Symbol, timeframe: str, klines: list[Kline]) -> None:
        key = f"{symbol.binance()}_{timeframe}"
        self._kline_cache[key] = sorted(klines, key=lambda k: k.timestamp)

    @staticmethod
    def _extra_tf_offset(timeframe: str) -> timedelta | None:
        tf_minutes = {"1w": 10080, "1d": 1440, "4h": 240, "1h": 60}
        minutes = tf_minutes.get(timeframe, 0)
        if minutes == 0:
            return None
        return timedelta(minutes=minutes * 100)

    def get_all_klines(self) -> list[Kline]:
        """Get all klines for the primary timeframe, sorted by timestamp."""
        all_klines: list[Kline] = []
        for klines in self._kline_cache.values():
            all_klines.extend(klines)
        return sorted(all_klines, key=lambda k: k.timestamp)
```

Update `fetch_ohlcv` to read from `_kline_cache` instead of `historical_data`:

```python
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        key = f"{symbol.binance()}_{timeframe}"
        if key not in self._kline_cache:
            return []

        klines = self._kline_cache[key]

        if start_time is not None or end_time is not None:
            klines = [k for k in klines
                      if (start_time is None or k.timestamp >= start_time)
                      and (end_time is None or k.timestamp <= end_time)]
            return klines[-limit:] if len(klines) > limit else klines

        current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]
        if not current_klines:
            return []

        return current_klines[-limit:] if len(current_klines) >= limit else current_klines
```

Add the required imports at the top of `backtest/backtest_client.py`:

```python
from datetime import timedelta
from backtest.kline_data_store import KlineDataStore
```

Remove `load_historical_data` method entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_backtest_rewrite.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_client.py test/test_backtest_rewrite.py
git commit -m "feat: BacktestClient internalizes KlineDataStore, auto-loads data"
```

---

### Task 2: Update existing tests to use new BacktestClient API

**Files:**
- Modify: `test/test_backtest_rewrite.py`
- Modify: `test/test_backtest_client.py`
- Modify: `test/test_backtest_event_loop.py`

- [ ] **Step 1: Update test_backtest_rewrite.py — replace `load_historical_data` / `historical_data` usage**

In `test/test_backtest_rewrite.py`, the test helpers and test classes use `client.load_historical_data(SYMBOL, '1m', klines)` and `client.historical_data[...]`. Replace these with the new `_store_klines` internal method for test convenience.

Add a helper function at the top of the test file (after `_make_klines`):

```python
def _client_with_data(kline_count: int = 10) -> BacktestClient:
    """Create a BacktestClient with pre-loaded kline data for testing."""
    client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
    klines = _make_klines(kline_count)
    client._store_klines(SYMBOL, '1m', klines)
    return client
```

Then replace all occurrences of the pattern:
```python
client = BacktestClient(order_repo=InMemoryOrderRepository())
klines = _make_klines(10)
client.load_historical_data(SYMBOL, '1m', klines)
```
With:
```python
client = _client_with_data(10)
```

And remove the separate `klines = _make_klines(...)` line where no longer needed.

In `TestBacktestClientCleanup::test_load_historical_data_no_lock`, change:
```python
    def test_load_historical_data_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(5)
        client.load_historical_data(SYMBOL, '1m', klines)
        assert len(client.historical_data[f"{SYMBOL.binance()}_1m"]) == 5
```
To:
```python
    def test_store_klines_no_lock(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        klines = _make_klines(5)
        client._store_klines(SYMBOL, '1m', klines)
        assert len(client._kline_cache[f"{SYMBOL.binance()}_1m"]) == 5
```

Similarly update `test_fetch_ohlcv_no_lock` and all `TestFetchOhlcvTimeParams` tests to use `_client_with_data()`.

- [ ] **Step 2: Update test_backtest_client.py**

In `test/test_backtest_client.py`, any test that calls `client.load_historical_data(...)` should use `client._store_klines(...)` instead, and any reference to `client.historical_data` should use `client._kline_cache`.

Search for and replace these patterns in the file.

- [ ] **Step 3: Update test_backtest_event_loop.py**

Check `test/test_backtest_event_loop.py` for any `load_historical_data` or `historical_data` references. If found, update similarly. If not found, skip.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest test/ -v --ignore=test/test_ccxt.py --ignore=test/test_limit_order_chaser.py --ignore=test/test_openai.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add test/test_backtest_rewrite.py test/test_backtest_client.py test/test_backtest_event_loop.py
git commit -m "refactor: update tests to use BacktestClient._store_klines instead of load_historical_data"
```

---

### Task 3: Rename BacktestAnalysis → TradeAnalysis with generic ExSwapClient

**Files:**
- Rename: `backtest/backtest_analysis.py` → `backtest/trade_analysis.py`
- Modify: `backtest/trade_analysis.py`
- Rename: `test/test_backtest_analysis.py` → `test/test_trade_analysis.py`
- Modify: `test/test_trade_analysis.py`

- [ ] **Step 1: Create the new file `backtest/trade_analysis.py`**

```python
from typing import Any

from backtest.analyzer import BacktestAnalyzer
from client.ex_client import ExSwapClient


class TradeAnalysis:
    """从 ExSwapClient 状态数据生成交易分析（通用，不限于回测）"""

    def __init__(self, client: ExSwapClient, initial_balance: float | None = None) -> None:
        self.client = client
        self.initial_balance = initial_balance if initial_balance is not None else client.balance('USDT')
        self._analyzer = BacktestAnalyzer(self.initial_balance)

    def analyze(self) -> dict[str, Any]:
        trade_history = self.client.get_trade_history()
        positions = self.client.positions()
        final_balance = self.client.balance('USDT')

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

Key changes from `BacktestAnalysis`:
- Accept `ExSwapClient` instead of `BacktestClient`
- `initial_balance` is optional — defaults to `client.balance('USDT')`
- Uses `client.balance('USDT')` instead of `client.get_final_balance()` (the latter is BacktestClient-specific)
- Uses `client.get_trade_history()` (available on BacktestClient, not on ExSwapClient base — see note below)

**Important:** `ExSwapClient` doesn't have `get_trade_history()` in its base interface. This method currently only exists on `BacktestClient`. We need to add it to `ExSwapClient` as an abstract method, or use a protocol. The simplest approach: add `get_trade_history` to `ExSwapClient`.

- [ ] **Step 2: Add `get_trade_history` to `ExSwapClient`**

In `client/ex_client.py`, add to `ExSwapClient` class:

```python
class ExSwapClient(ExClient):

    @abstractmethod
    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        pass

    @abstractmethod
    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        pass

    def get_trade_history(self) -> list[dict[str, Any]]:
        """Return all filled trade history. Override in subclasses for custom behavior."""
        return []
```

Then add `get_trade_history` override to `BinanceSwapClient` in `client/binance_client.py`:

```python
    def get_trade_history(self) -> list[dict[str, Any]]:
        return [self._order_to_dict(o) for o in self.order_repo.find_history()]
```

Also add `_order_to_dict` static method to `BinanceSwapClient` (same as BacktestClient's implementation):

```python
    @staticmethod
    def _order_to_dict(order: Order) -> dict[str, Any]:
        return {
            'id': order.order_id,
            'clientOrderId': order.order_id,
            'symbol': order.symbol.binance(),
            'side': order.side.value,
            'position_side': order.position_side.value,
            'type': order.order_type,
            'price': order.price,
            'amount': order.quantity,
            'filled': order.filled_quantity,
            'filled_quantity': order.filled_quantity,
            'remaining': order.quantity - order.filled_quantity,
            'filled_price': order.filled_price,
            'cost': order.filled_price * order.filled_quantity if order.filled_price else 0,
            'status': order.status.value,
            'timestamp': order.created_at,
            'fee': order.fee,
        }
```

Add `Kline` to imports in `client/binance_client.py` (if not already there from phase 1).

- [ ] **Step 3: Create `test/test_trade_analysis.py`**

```python
import pytest
from typing import Any
from model import Symbol, OrderSide, PositionSide
from backtest.backtest_client import BacktestClient
from backtest.trade_analysis import TradeAnalysis
from client.ex_client import ExSwapClient
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


class TestTradeAnalysis:
    def test_analyze_returns_dict(self) -> None:
        client = _client_with_trade()
        analysis = TradeAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert isinstance(result, dict)
        assert 'summary' in result
        assert 'risk_metrics' in result
        assert 'trade_metrics' in result
        assert 'final_state' in result

    def test_final_state_includes_balance(self) -> None:
        client = _client_with_trade()
        analysis = TradeAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert 'balance' in result['final_state']
        assert 'pnl' in result['final_state']

    def test_report_returns_string(self) -> None:
        client = _client_with_trade()
        analysis = TradeAnalysis(client, initial_balance=10_000.0)
        report = analysis.report()
        assert isinstance(report, str)
        assert "BACKTEST REPORT" in report

    def test_empty_backtest(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
        analysis = TradeAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert result['summary']['total_trades'] == 0
        assert result['final_state']['balance'] == 10_000.0

    def test_initial_balance_defaults_to_client_balance(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=5_000.0)
        analysis = TradeAnalysis(client)
        assert analysis.initial_balance == 5_000.0

    def test_accepts_ex_swap_client_type(self) -> None:
        """TradeAnalysis type hint accepts ExSwapClient, not just BacktestClient."""
        client = _client_with_trade()
        # This tests that TradeAnalysis.__init__ accepts ExSwapClient-typed arg
        typed_client: ExSwapClient = client
        analysis = TradeAnalysis(typed_client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert isinstance(result, dict)
```

- [ ] **Step 4: Delete old files**

```bash
rm backtest/backtest_analysis.py
rm test/test_backtest_analysis.py
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest test/test_trade_analysis.py test/test_backtest_rewrite.py -v
```

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backtest/trade_analysis.py backtest/backtest_analysis.py client/ex_client.py client/binance_client.py test/test_trade_analysis.py test/test_backtest_analysis.py
git commit -m "feat: rename BacktestAnalysis to TradeAnalysis, accept generic ExSwapClient"
```

---

### Task 4: Delete BacktestRunner and update all consumers

**Files:**
- Delete: `backtest/runner.py`
- Modify: `api/routes/backtest.py`
- Modify: `run_backtest.py`
- Modify: `run_alpha_trend_backtest.py`
- Modify: `backtest_signal_grid.py`

- [ ] **Step 1: Update `api/routes/backtest.py`**

The API route currently uses `BacktestRunner`. Replace the `run_backtest` endpoint logic to inline the BacktestRunner orchestration using the new BacktestClient + BacktestEventLoop + TradeAnalysis.

The key change: replace `BacktestRunner(config, strategy_factory=...).run()` with inline BacktestClient + BacktestEventLoop + TradeAnalysis.

Replace the `run_backtest` endpoint's try block (lines 206-211) with:

```python
    try:
        data_store = KlineDataStore()
        client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=request.initial_balance,
            data_store=data_store,
            symbol=symbol,
            timeframe=request.timeframe,
            start_date=request.start_date,
            end_date=request.end_date,
            extra_timeframes=extra_timeframes,
        )

        all_klines = client.get_all_klines()
        if not all_klines:
            raise HTTPException(status_code=500, detail="No historical data loaded")

        strategy = strategy_factory(client)
        handler = KlineHandler(strategy)
        start_ts = _parse_start_timestamp(request.start_date)

        event_loop = BacktestEventLoop(
            historical_klines=all_klines,
            start_timestamp=start_ts,
        )
        event_loop.set_backtest_client(client)
        event_loop.add_handler(handler)
        event_loop.start()
        event_loop.stop()

        trade_analysis = TradeAnalysis(client, initial_balance=request.initial_balance)
        analysis = trade_analysis.analyze()

        result = BacktestResult(
            analysis=analysis,
            trade_history=client.get_trade_history(),
            final_balance=client.get_final_balance(),
            report=trade_analysis.report(),
        )
```

Add this helper function before the `run_backtest` endpoint:

```python
def _parse_start_timestamp(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
```

Update imports at the top of `api/routes/backtest.py`:
```python
# Remove:
from backtest.runner import BacktestRunner

# Add:
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.trade_analysis import TradeAnalysis
from backtest.kline_data_store import KlineDataStore
from backtest.result import BacktestResult
from event_loop.handler.kline_handler import KlineHandler
from persistence.order_repository import InMemoryOrderRepository
from datetime import datetime, timezone
```

- [ ] **Step 2: Update `run_backtest.py`**

The `run_generic_backtest` function currently loads data via `KlineDataStore` and calls `client.load_historical_data()`. Update it to use the new BacktestClient constructor that auto-loads data.

Replace the data loading section in `run_generic_backtest`:

From:
```python
        data_loader = KlineDataStore()
        ...
        for symbol, timeframe in data_requirements:
            file_path = data_loader.ensure_data(symbol, timeframe, start_time, end_time, data_dir, offset=data_offset)
            klines = data_loader.load_csv(file_path, symbol, timeframe)
            if data_offset:
                klines = [k for k in klines
                          if (start_timestamp is None or k.timestamp >= start_timestamp)
                          and (end_timestamp is None or k.timestamp <= end_timestamp)]
            ...
            backtest_client.load_historical_data(symbol, timeframe, klines)
            all_klines.extend(klines)
```

To:
```python
        # Use first data requirement as primary, rest as extra_timeframes
        primary_symbol, primary_tf = data_requirements[0]
        extra_tfs = tuple(tf for _, tf in data_requirements[1:])

        data_store = KlineDataStore()
        backtest_client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=initial_balance,
            maker_fee=0.0002,
            taker_fee=0.0004,
            data_store=data_store,
            symbol=primary_symbol,
            timeframe=primary_tf,
            start_date=start_time,
            end_date=end_time,
            extra_timeframes=extra_tfs,
            data_dir=data_dir,
        )

        all_klines = backtest_client.get_all_klines()
```

Also update the analysis section at the end:

From:
```python
        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)
        ...
        report = analyzer.generate_report(analysis, report_file)
```

To:
```python
        from backtest.trade_analysis import TradeAnalysis
        trade_analysis = TradeAnalysis(backtest_client, initial_balance=initial_balance)
        analysis = trade_analysis.analyze()
        report = trade_analysis.report()
        ...
        # Save report to file
        if report_file:
            with open(report_file, 'w') as f:
                f.write(report)
```

Remove the `BacktestAnalyzer` import and `from backtest.kline_data_store import KlineDataStore` if no longer used directly.

- [ ] **Step 3: Update `run_alpha_trend_backtest.py`**

This script is more complex — it uses `MultiTimeframeBacktestEventLoop` and `BacktestHandler`. The key change is replacing `client.load_historical_data()` calls with the new auto-load constructor.

Update the backtest_client creation:

From:
```python
        backtest_client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=initial_balance,
            maker_fee=0.0005,
            taker_fee=0.0005
        )
```

To (keeping the manual data loading since this script uses a different event loop pattern):
```python
        backtest_client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=initial_balance,
            maker_fee=0.0005,
            taker_fee=0.0005,
        )
```

Then replace `data_loader = KlineDataStore()` + `data_loader.load_csv()` + manual `historical_data` dict with:
```python
        data_store = KlineDataStore()
        historical_data = {}

        for timeframe, file_path in data_files.items():
            klines = data_store.load_csv(file_path, symbol, timeframe)
            historical_data[timeframe] = klines
            backtest_client._store_klines(symbol, timeframe, klines)
            logger.info(f"加载了 {len(klines)} 根{timeframe} K线数据")
```

And replace the `BacktestAnalyzer` usage at the end:

From:
```python
        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)
        report = analyzer.generate_report(analysis, report_file)
```

To:
```python
        from backtest.trade_analysis import TradeAnalysis
        trade_analysis = TradeAnalysis(backtest_client, initial_balance=initial_balance)
        analysis = trade_analysis.analyze()
        report = trade_analysis.report()
        if report_file:
            with open(report_file, 'w') as f:
                f.write(report)
```

- [ ] **Step 4: Update `backtest_signal_grid.py`**

Similar pattern. Replace `data_loader.load_csv()` + `backtest_client` pattern:

From:
```python
        data_loader = KlineDataStore()
        historical_klines = data_loader.load_csv(data_file, symbol, timeframe)
```

To:
```python
        data_store = KlineDataStore()
        historical_klines = data_store.load_csv(data_file, symbol, timeframe)
```

Then after creating `backtest_client`, add:
```python
        backtest_client._store_klines(symbol, timeframe, historical_klines)
```

Replace `BacktestAnalyzer` usage similarly to step 3.

- [ ] **Step 5: Delete `backtest/runner.py`**

```bash
rm backtest/runner.py
```

- [ ] **Step 6: Run all tests**

```bash
uv run pytest test/ -v --ignore=test/test_ccxt.py --ignore=test/test_limit_order_chaser.py --ignore=test/test_openai.py
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete BacktestRunner, update all consumers to use new BacktestClient"
```

---

### Task 5: Update BacktestConfig and remaining imports

**Files:**
- Modify: `backtest/config.py`
- Modify: `backtest/__init__.py`
- Verify all imports

- [ ] **Step 1: Add `data_dir` to BacktestConfig**

In `backtest/config.py`, add `data_dir` field:

```python
@dataclass(frozen=True)
class BacktestConfig:
    strategy_type: str
    strategy_config: dict[str, str | int | float | bool | list | dict]
    symbol: Symbol
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
    extra_timeframes: tuple[str, ...] = ()
    data_dir: str = "data"
```

- [ ] **Step 2: Verify no remaining references to deleted modules**

```bash
grep -rn "from backtest.runner\|BacktestRunner\|from backtest.backtest_analysis\|BacktestAnalysis\|load_historical_data\|historical_data" --include="*.py" . | grep -v __pycache__ | grep -v ".venv"
```

Expected: No results (or only references in files being updated in this task).

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest test/ -v --ignore=test/test_ccxt.py --ignore=test/test_limit_order_chaser.py --ignore=test/test_openai.py
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add backtest/config.py
git commit -m "refactor: add data_dir to BacktestConfig, clean up remaining imports"
```

---

### Task 6: Full integration verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest test/ -v --ignore=test/test_ccxt.py --ignore=test/test_limit_order_chaser.py --ignore=test/test_openai.py
```

Expected: ALL PASS

- [ ] **Step 2: Verify BacktestClient end-to-end flow**

Create a quick smoke test script and run it:

```bash
uv run python -c "
from backtest.backtest_client import BacktestClient
from backtest.trade_analysis import TradeAnalysis
from persistence.order_repository import InMemoryOrderRepository

client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
print(f'BacktestClient created, balance: {client.balance(\"USDT\")}')

analysis = TradeAnalysis(client, initial_balance=10_000.0)
result = analysis.analyze()
print(f'TradeAnalysis works, total_trades: {result[\"summary\"][\"total_trades\"]}')
print('All OK')
"
```

Expected: `All OK`

- [ ] **Step 3: Verify no stale files remain**

```bash
ls backtest/runner.py backtest/backtest_analysis.py test/test_backtest_analysis.py 2>&1
```

Expected: "No such file or directory" for all three
