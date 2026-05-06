# BacktestClient 懒加载重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove data-loading parameters from BacktestClient constructor, implement lazy loading in `fetch_ohlcv` so data is loaded on-demand like a real exchange client.

**Architecture:** BacktestClient becomes a pure simulated exchange — its constructor only takes infrastructure dependencies (data_store, order_repo, balance, fees). When `fetch_ohlcv` is called with no cached data, it uses `current_timestamp` + `limit` + `timeframe` to compute a date range, loads from `KlineDataStore`, and caches the result.

**Tech Stack:** Python 3.11, pytest, ccxt (for `parse_timeframe`)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backtest/client.py` | Modify | Remove data params from `__init__`, delete `_load_data_from_store`/`_extra_tf_offset`, add lazy loading in `fetch_ohlcv` |
| `backtest/runner.py` | Modify | Remove data params from `_create_backtest_client` |
| `api/routes/backtest.py` | Modify | Remove data params from `BacktestClient()` construction |
| `tests/test_backtest_client.py` | Modify | Fix broken `_client()` helper, add lazy loading tests |

---

### Task 1: Add `_ensure_klines` lazy loading method to BacktestClient

**Files:**
- Modify: `backtest/client.py:26-98`
- Test: `tests/test_backtest_client.py`

- [ ] **Step 1: Write the failing test for lazy loading**

Add this test class to `tests/test_backtest_client.py`:

```python
from unittest.mock import MagicMock
from persistence.kline_data_store import KlineDataStore


class TestFetchOhlcvLazyLoading:
    def test_fetch_ohlcv_loads_data_from_store_when_cache_miss(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)
        mock_store.ensure_data.return_value = "data/mock.csv"
        mock_store.load_csv.return_value = klines

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE + 4 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)

        mock_store.ensure_data.assert_called_once()
        mock_store.load_csv.assert_called_once_with("data/mock.csv", SYMBOL, '1m')
        assert len(result) == 5

    def test_fetch_ohlcv_uses_cache_on_second_call(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)
        mock_store.ensure_data.return_value = "data/mock.csv"
        mock_store.load_csv.return_value = klines

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE + 4 * 60_000)

        client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        client.fetch_ohlcv(SYMBOL, '1m', limit=5)

        mock_store.ensure_data.assert_called_once()
        mock_store.load_csv.assert_called_once()

    def test_fetch_ohlcv_returns_empty_when_no_data_store(self):
        repo = InMemoryOrderRepository()
        client = BacktestClient(order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        assert result == []

    def test_fetch_ohlcv_no_lazy_load_when_cache_hit(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client._store_klines(SYMBOL, '1m', klines)
        client.update_current_timestamp(TS_BASE + 4 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        mock_store.ensure_data.assert_not_called()
        assert len(result) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest_client.py::TestFetchOhlcvLazyLoading -v`
Expected: FAIL — `_ensure_klines` method doesn't exist yet, `data_store` not optional

- [ ] **Step 3: Refactor `__init__` and add `_ensure_klines` method**

In `backtest/client.py`, make these changes:

**3a.** Change `__init__` to make `data_store` optional and remove data-loading params:

```python
def __init__(
    self,
    order_repo: OrderRepository,
    data_store: KlineDataStore | None = None,
    initial_balance: float = 10000.0,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0004,
    symbol_infos: dict[str, SymbolInfo] | None = None,
) -> None:
    self.exchange_name = 'backtest'
    self.exchange = None  # type: ignore

    self.data_store = data_store
    self.order_repo = order_repo

    self.initial_balance = initial_balance
    self._balance = initial_balance
    self.maker_fee = maker_fee
    self.taker_fee = taker_fee

    self._symbol_infos: dict[str, SymbolInfo] = symbol_infos or {}

    self._positions: dict[str, _Position] = {}

    self.current_prices: dict[str, float] = {}
    self.current_timestamp: int = 0

    self._kline_cache: dict[str, list[Kline]] = {}

    logger.info("BacktestClient initialized with balance: %s", initial_balance)
```

**3b.** Delete the `_load_data_from_store` method and the `_extra_tf_offset` static method entirely.

**3c.** Add the `_ensure_klines` method:

```python
def _ensure_klines(self, symbol: Symbol, timeframe: str, limit: int) -> None:
    """Lazy-load klines from data_store if not cached."""
    key = f"{symbol.binance()}_{timeframe}"
    if key in self._kline_cache:
        return

    if self.data_store is None:
        logger.warning("No data_store configured, cannot load %s %s", symbol.binance(), timeframe)
        return

    tf_ms = self._parse_timeframe_to_ms(timeframe)
    if tf_ms == 0:
        logger.warning("Unknown timeframe: %s", timeframe)
        return

    buffer_ratio = 1.3
    range_ms = int(limit * tf_ms * buffer_ratio)
    end_ts = self.current_timestamp
    start_ts = end_ts - range_ms

    from datetime import datetime, timezone
    start_date = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    end_date = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')

    try:
        file_path = self.data_store.ensure_data(symbol, timeframe, start_date, end_date)
        klines = self.data_store.load_csv(file_path, symbol, timeframe)
        if klines:
            self._store_klines(symbol, timeframe, klines)
            logger.info("Lazy-loaded %d klines for %s %s", len(klines), symbol.binance(), timeframe)
    except FileNotFoundError:
        logger.warning("Data file not found for %s %s", symbol.binance(), timeframe)
    except ValueError as e:
        logger.warning("Failed to load data for %s %s: %s", symbol.binance(), timeframe, e)

@staticmethod
def _parse_timeframe_to_ms(timeframe: str) -> int:
    """Convert timeframe string (e.g. '5m', '1h', '1d') to milliseconds."""
    try:
        import ccxt
        return ccxt.Exchange.parse_timeframe(timeframe) * 1000
    except Exception:
        return 0
```

**3d.** Update `fetch_ohlcv` to call `_ensure_klines` before reading cache. Replace the current `fetch_ohlcv` method body with:

```python
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
    self._ensure_klines(symbol, timeframe, limit)

    key = f"{symbol.binance()}_{timeframe}"
    if key not in self._kline_cache:
        return []

    klines = self._kline_cache[key]

    if start_time is not None or end_time is not None:
        klines = [k for k in klines
                  if (start_time is None or k.timestamp >= start_time)
                  and (end_time is None or k.timestamp < end_time)]
        if limit and len(klines) > limit:
            return klines[-limit:]
        return klines

    current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]
    if not current_klines:
        logger.warning("No klines available before timestamp %d for timeframe %s",
                      self.current_timestamp, timeframe)
        return []

    if limit and len(current_klines) >= limit:
        return current_klines[-limit:]
    return current_klines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtest_client.py::TestFetchOhlcvLazyLoading -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest/client.py tests/test_backtest_client.py
git commit -m "feat: add lazy loading to BacktestClient.fetch_ohlcv"
```

---

### Task 2: Fix existing tests that don't pass `data_store`

**Files:**
- Modify: `tests/test_backtest_client.py:25-31`

- [ ] **Step 1: Fix `_client()` helper**

The `_client()` helper currently calls `BacktestClient(order_repo=repo, initial_balance=10_000.0)` without `data_store`. After Task 1, `data_store` is optional, so this now works. But these tests don't need data loading — they only test order/trading logic. No change needed to the helper itself.

Verify by running:

Run: `uv run pytest tests/test_backtest_client.py -v`
Expected: 17 PASSED (13 old + 4 new)

- [ ] **Step 2: Commit if any changes were needed** (likely none if Task 1 made data_store optional)

```bash
git add tests/test_backtest_client.py
git commit -m "fix: update test helpers for BacktestClient without data_store"
```

---

### Task 3: Update BacktestRunner to stop passing data params

**Files:**
- Modify: `backtest/runner.py:34-47`
- Test: `tests/test_backtest_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backtest_runner.py`:

```python
class TestBacktestRunnerNoDataParams:
    def test_runner_does_not_pass_data_params_to_client(self):
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.runner.KlineDataStore') as mock_store_cls, \
             patch('backtest.runner.BacktestClient') as mock_client_cls:
            mock_store_cls.return_value = MagicMock()
            mock_client_cls.return_value = MagicMock()

            BacktestRunner(config)

            call_kwargs = mock_client_cls.call_args[1]
            assert 'symbol' not in call_kwargs
            assert 'timeframe' not in call_kwargs
            assert 'start_date' not in call_kwargs
            assert 'end_date' not in call_kwargs
            assert 'extra_timeframes' not in call_kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest_runner.py::TestBacktestRunnerNoDataParams -v`
Expected: FAIL — BacktestRunner still passes data params

- [ ] **Step 3: Update `_create_backtest_client` in `backtest/runner.py`**

Replace the method:

```python
def _create_backtest_client(self) -> BacktestClient:
    data_store = KlineDataStore()
    return BacktestClient(
        order_repo=InMemoryOrderRepository(),
        data_store=data_store,
        initial_balance=self._config.initial_balance,
        maker_fee=self._config.maker_fee,
        taker_fee=self._config.taker_fee,
    )
```

- [ ] **Step 4: Run all runner tests**

Run: `uv run pytest tests/test_backtest_runner.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest/runner.py tests/test_backtest_runner.py
git commit -m "refactor: remove data params from BacktestRunner._create_backtest_client"
```

---

### Task 4: Update API route to stop passing data params

**Files:**
- Modify: `api/routes/backtest.py:199-209`

- [ ] **Step 1: Update `BacktestClient` construction in API route**

In `api/routes/backtest.py`, replace the BacktestClient construction block (lines 199-209):

```python
        data_store = KlineDataStore()
        client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            data_store=data_store,
            initial_balance=request.initial_balance,
        )
```

- [ ] **Step 2: Run API-related tests if they exist, otherwise verify import**

Run: `uv run python -c "from api.routes.backtest import router; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add api/routes/backtest.py
git commit -m "refactor: remove data params from BacktestClient in API route"
```

---

### Task 5: Update event loop tests and verify full integration

**Files:**
- Test: `tests/test_backtest_event_loop.py`

- [ ] **Step 1: Run event loop tests to verify they still pass**

Run: `uv run pytest tests/test_backtest_event_loop.py -v`
Expected: All PASSED (event loop tests use `_store_klines` directly, no data_store dependency)

- [ ] **Step 2: Run full backtest test suite**

Run: `uv run pytest tests/test_backtest_client.py tests/test_backtest_event_loop.py tests/test_backtest_runner.py -v`
Expected: All PASSED

- [ ] **Step 3: Run mypy type check**

Run: `uv run mypy backtest/client.py backtest/runner.py`
Expected: No errors

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: resolve type errors and test failures from lazy loading refactor"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Remove `symbol/timeframe/start_date/end_date/extra_timeframes` from `__init__` → Task 1 Step 3a ✓
- Delete `_load_data_from_store` → Task 1 Step 3b ✓
- Delete `_extra_tf_offset` → Task 1 Step 3b ✓
- Lazy loading in `fetch_ohlcv` → Task 1 Step 3c + 3d ✓
- Runner adaptation → Task 3 ✓
- EventLoop adaptation → Spec says "no change needed", verified in Task 5 ✓
- API route adaptation → Task 4 ✓

**2. Placeholder scan:** No TBD/TODO/fill-in-details found.

**3. Type consistency:**
- `data_store: KlineDataStore | None` in `__init__` matches `self.data_store is None` check in `_ensure_klines` ✓
- `_ensure_klines(symbol: Symbol, timeframe: str, limit: int)` matches `fetch_ohlcv` call site ✓
- `_parse_timeframe_to_ms` returns `int`, checked against `0` ✓
