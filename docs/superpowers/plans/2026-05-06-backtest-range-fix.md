# Backtest Range Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix BacktestEventLoop to respect BacktestConfig's start_date/end_date range for both data loading and loop termination.

**Architecture:** BacktestEventLoop receives BacktestConfig, parses start_date/end_date to timestamps, uses them to (1) load only in-range klines from BacktestClient.fetch_ohlcv, (2) resolve start_index and end_index for the replay loop. The warmup logic (skipping first 300 klines) is preserved via start_index resolution.

**Tech Stack:** Python 3.12+, pytest

---

### Task 1: Add _parse_date_to_timestamp helper and update BacktestEventLoop constructor

**Files:**
- Modify: `backtest/backtest_event_loop.py`

- [ ] **Step 1: Add the helper function**

Add at top of file, after imports:

```python
from datetime import datetime, timezone


def _parse_date_to_timestamp(date_str: str) -> int:
    """将 YYYY-MM-DD 格式转为 UTC 毫秒时间戳"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
```

- [ ] **Step 2: Rewrite BacktestEventLoop.__init__ to accept config**

Replace the entire `__init__` method:

```python
def __init__(self,
             config: BacktestConfig,
             on_progress_callback: Callable[[int, int], None] | None = None) -> None:
    super().__init__()
    self._config = config
    self._start_ts = _parse_date_to_timestamp(config.start_date)
    self._end_ts = _parse_date_to_timestamp(config.end_date)
    self._subscriptions: dict[str, tuple[Symbol, str]] = {}
    self.on_progress_callback = on_progress_callback
    self.start_index = 0
    self.end_index = 0
    self.current_index = 0
    self.is_running = False
    self.backtest_client: BacktestClient | None = None
    self.historical_klines: list[Kline] = []
```

Remove `start_timestamp`, `start_index`, `historical_klines` constructor params. Remove `_start_timestamp` and `_start_index_override` instance vars. Add `end_index` and `_config`, `_start_ts`, `_end_ts`.

- [ ] **Step 3: Run existing tests to see what breaks**

Run: `uv run pytest tests/test_backtest_event_loop.py tests/test_backtest_runner.py -v`
Expected: FAIL (constructor signature changed)

### Task 2: Fix _load_subscribed_klines and _resolve_start_index, add _resolve_end_index

**Files:**
- Modify: `backtest/backtest_event_loop.py`

- [ ] **Step 1: Fix _load_subscribed_klines to pass time range and fix iteration bug**

Replace the entire `_load_subscribed_klines` method:

```python
def _load_subscribed_klines(self) -> list[Kline]:
    if not self.backtest_client:
        return []
    collected: list[Kline] = []

    for key, (symbol, tf) in self._subscriptions.items():
        klines = self.backtest_client.fetch_ohlcv(
            symbol, tf,
            start_time=self._start_ts,
            end_time=self._end_ts,
            limit=0,
        )
        collected.extend(klines)

    return sorted(collected, key=lambda k: k.timestamp)
```

Note: `limit=0` signals "no limit" — will handle in Task 3.

- [ ] **Step 2: Rewrite _resolve_start_index to use config**

Replace the entire `_resolve_start_index` method:

```python
def _resolve_start_index(self) -> None:
    default_warmup = min(300, len(self.historical_klines) - 1)
    for i, kline in enumerate(self.historical_klines):
        if kline.timestamp >= self._start_ts:
            self.start_index = i
            return
    self.start_index = default_warmup
```

The warmup skip is implicit: if start_date maps to kline index 300+, the loop starts there. If start_date is before the data, the loop starts from index 0. The `start_index` field from `BacktestConfig` (default 300) is no longer used — `start_date` is the source of truth.

- [ ] **Step 3: Add _resolve_end_index method**

Add after `_resolve_start_index`:

```python
def _resolve_end_index(self) -> None:
    for i, kline in enumerate(self.historical_klines):
        if kline.timestamp >= self._end_ts:
            self.end_index = i
            return
    self.end_index = len(self.historical_klines)
```

- [ ] **Step 4: Update start() to call _resolve_end_index**

In `start()`, add `self._resolve_end_index()` call after `self._resolve_start_index()`, and use `self.end_index` in the log message:

```python
def start(self) -> None:
    """开始回测（同步执行，阻塞直到完成）"""
    if self.is_running:
        logger.warning("Backtest already running")
        return

    self.historical_klines = self._load_subscribed_klines()

    if not self.historical_klines:
        logger.warning("No historical data available")
        return

    self._resolve_start_index()
    self._resolve_end_index()

    self.is_running = True
    self.current_index = self.start_index

    logger.info("Backtest started from index %d to %d (%d klines loaded)",
                self.start_index, self.end_index, len(self.historical_klines))
    self._run_backtest_sync()
```

- [ ] **Step 5: Update _run_backtest_sync to use end_index**

Change the while condition:

```python
def _run_backtest_sync(self) -> None:
    while self.is_running and self.current_index < self.end_index:
        self._process_next_kline()

        if self.on_progress_callback:
            self.on_progress_callback(self.current_index, len(self.historical_klines))

    self.is_running = False
    logger.info("Backtest completed")
```

- [ ] **Step 6: Update progress property to use end_index**

Replace the `progress` property:

```python
@property
def progress(self) -> float:
    if not self.historical_klines:
        return 0.0
    total_backtest_klines = self.end_index - self.start_index
    if total_backtest_klines <= 0:
        return 1.0
    current_backtest_index = self.current_index - self.start_index
    return min(1.0, max(0.0, current_backtest_index / total_backtest_klines))
```

- [ ] **Step 7: Update is_completed property to use end_index**

```python
@property
def is_completed(self) -> bool:
    return self.current_index >= self.end_index
```

### Task 3: Update BacktestClient.fetch_ohlcv to support limit=0 as "no limit"

**Files:**
- Modify: `backtest/backtest_client.py`

- [ ] **Step 1: Update fetch_ohlcv to treat limit=0 as unlimited**

In the `fetch_ohlcv` method, change the `start_time`/`end_time` branch (line 340-344):

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

Key change: `if limit and len(klines) > limit:` — `limit=0` is falsy, so it skips truncation.

Also changed `end_time` filter from `<=` to `<` — end_date is exclusive (matches the convention that "2025-02-01" means data up to but not including Feb 1).

- [ ] **Step 2: Write a failing test for limit=0**

In `tests/test_backtest_event_loop.py`, add:

```python
def test_fetch_ohlcv_limit_zero_returns_all() -> None:
    """fetch_ohlcv with limit=0 should return all matching klines without truncation"""
    klines = _make_klines(200)
    client = BacktestClient(order_repo=InMemoryOrderRepository())
    client._store_klines(SYMBOL, '1m', klines)

    result = client.fetch_ohlcv(SYMBOL, '1m', limit=0)
    assert len(result) == 200
```

- [ ] **Step 3: Run the new test**

Run: `uv run pytest tests/test_backtest_event_loop.py::test_fetch_ohlcv_limit_zero_returns_all -v`
Expected: PASS (the implementation in Step 1 already handles it)

### Task 4: Update BacktestRunner to pass config to BacktestEventLoop

**Files:**
- Modify: `backtest/backtest_runner.py`

- [ ] **Step 1: Update BacktestRunner.__init__ to pass config**

In `BacktestRunner.__init__`, replace:

```python
self._event_loop = BacktestEventLoop(
    start_index=config.start_index,
)
```

with:

```python
self._event_loop = BacktestEventLoop(config=config)
```

- [ ] **Step 2: Update BacktestConfig — remove start_index field**

In `backtest/backtest_runner.py`, remove the `start_index: int = 300` field from `BacktestConfig` dataclass. The start position is now derived from `start_date`.

- [ ] **Step 3: Update run.py — remove start_index if referenced**

Check `run.py` for `start_index` usage. If it's in `BacktestConfig` construction, remove it. (Current `run.py` does not pass `start_index`, so no change needed — but verify.)

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_event_loop.py backtest/backtest_runner.py backtest/backtest_client.py
git commit -m "fix: BacktestEventLoop respects start_date/end_date for data loading and loop termination"
```

### Task 5: Update API route to use new BacktestEventLoop constructor

**Files:**
- Modify: `api/routes/backtest.py`

- [ ] **Step 1: Update BacktestEventLoop instantiation in run_backtest**

In `api/routes/backtest.py`, the route currently does:

```python
start_ts = _parse_start_timestamp(request.start_date)

event_loop = BacktestEventLoop(
    start_timestamp=start_ts,
)
```

Replace with a `BacktestConfig`-based approach. The API route doesn't use `BacktestRunner` so it needs its own config:

```python
from backtest.backtest_runner import BacktestConfig

config = BacktestConfig(
    symbol=symbol,
    timeframe=request.timeframe,
    start_date=request.start_date,
    end_date=request.end_date,
    initial_balance=request.initial_balance,
    extra_timeframes=extra_timeframes,
)
event_loop = BacktestEventLoop(config=config)
```

Remove the `start_ts = _parse_start_timestamp(request.start_date)` line and the `_parse_start_timestamp` function if no longer used elsewhere in the file.

- [ ] **Step 2: Commit**

```bash
git add api/routes/backtest.py
git commit -m "fix: API backtest route uses BacktestConfig for event loop"
```

### Task 6: Update existing tests to use new BacktestEventLoop constructor

**Files:**
- Modify: `tests/test_backtest_event_loop.py`
- Modify: `tests/test_backtest_runner.py`

- [ ] **Step 1: Update test_backtest_event_loop.py**

All tests currently use `BacktestEventLoop(historical_klines=klines, start_index=0)` or `BacktestEventLoop(start_timestamp=target_ts)`. Update to use `BacktestConfig`.

Add a helper at module level:

```python
from backtest.backtest_runner import BacktestConfig

# Use dates that map to TS_BASE range
START_DATE = '2023-11-14'
END_DATE = '2023-11-15'


def _make_config(**overrides) -> BacktestConfig:
    defaults = dict(
        symbol=SYMBOL,
        timeframe='1m',
        start_date=START_DATE,
        end_date=END_DATE,
    )
    return BacktestConfig(**(defaults | overrides))
```

Update test constructions:

- `BacktestEventLoop(historical_klines=klines, start_index=0)` → `BacktestEventLoop(config=_make_config())`
- `BacktestEventLoop(start_timestamp=target_ts)` → `BacktestEventLoop(config=_make_config(start_date='2023-11-14T03:00:00'))` — but since `_parse_date_to_timestamp` only accepts `YYYY-MM-DD`, use a date that matches the target timestamp range.

For `test_start_timestamp`, the test uses `target_ts = TS_BASE + 3 * 60_000`. Convert: `TS_BASE = 1_700_000_000_000` → `2023-11-14 22:13:20 UTC`. So `start_date='2023-11-15'` would start after all 10 klines (each is 1m apart, covering ~10 minutes). Instead, keep `start_date='2023-11-14'` and test that the loop starts from the first kline matching that date.

Update `test_start_timestamp` to verify start_date filtering works:

```python
def test_start_date_resolves_start_index(self) -> None:
    klines = _make_klines(10)
    # TS_BASE = 2023-11-14 22:13:20 UTC, klines span 10 minutes
    # Use start_date matching the 4th kline's date
    config = _make_config(start_date='2023-11-14')
    strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
    handler = KlineHandler(strategy)

    event_loop = BacktestEventLoop(config=config)
    event_loop.set_backtest_client(self._make_client_with_klines(klines))
    event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
    event_loop.add_handler(handler)
    event_loop.start()

    assert len(strategy.received_klines) > 0
    assert strategy.received_klines[0].timestamp >= _parse_date_to_timestamp('2023-11-14')
```

This requires tests that use `BacktestClient` with stored klines + subscribe flow instead of passing `historical_klines` directly. Update all integration tests to follow the subscribe pattern.

**Full rewrite of test class:**

```python
import pytest

from model import Symbol, Kline
from event_loop.event import KlineEvent
from event_loop.handler.kline_handler import KlineHandler
from backtest.backtest_event_loop import BacktestEventLoop, _parse_date_to_timestamp
from backtest.backtest_runner import BacktestConfig
from backtest.backtest_client import BacktestClient
from persistence.order_repository import InMemoryOrderRepository
from strategy import GeneralStrategy
from client.ex_client import ExClient


SYMBOL = Symbol(base='ETH', quote='USDT')
TS_BASE = 1_700_000_000_000
START_DATE = '2023-11-14'
END_DATE = '2023-11-15'


class MockExClient(ExClient):
    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100, start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        return []

    def balance(self, coin: str) -> float:
        return 0.0

    def cancel(self, custom_id: str, symbol: Symbol) -> dict:
        return {}

    def query_order(self, custom_id: str, symbol: Symbol) -> dict:
        return {}


class CollectorStrategy(GeneralStrategy):
    def __init__(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        super().__init__(symbols, timeframes)
        self.mock_client = MockExClient()
        self.received_klines: list[Kline] = []

    def exchange_client(self) -> ExClient:
        return self.mock_client

    def run(self, kline: Kline) -> None:
        self.received_klines.append(kline)


def _make_klines(count: int) -> list[Kline]:
    return [
        Kline(
            symbol=SYMBOL,
            timeframe='1m',
            open=2000.0 + i,
            high=2010.0 + i,
            low=1990.0 + i,
            close=2005.0 + i,
            volume=100.0,
            timestamp=TS_BASE + i * 60_000,
            finished=True,
        )
        for i in range(count)
    ]


def _make_config(**overrides) -> BacktestConfig:
    defaults = dict(
        symbol=SYMBOL,
        timeframe='1m',
        start_date=START_DATE,
        end_date=END_DATE,
    )
    return BacktestConfig(**(defaults | overrides))


def _make_client_with_klines(klines: list[Kline]) -> BacktestClient:
    client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
    client._store_klines(SYMBOL, '1m', klines)
    return client


class TestBacktestEventLoopIntegration:
    def test_passes_kline_events_without_json_serialization(self) -> None:
        klines = _make_klines(10)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(_make_client_with_klines(klines))
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.add_handler(handler)
        event_loop.start()

        assert len(strategy.received_klines) == 10

    def test_backtest_client_price_updates(self) -> None:
        klines = _make_klines(5)
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1m'])
        handler = KlineHandler(strategy)
        client = _make_client_with_klines(klines)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.add_handler(handler)
        event_loop.start()

        assert client.get_current_price(SYMBOL) == 2005.0 + 4
        assert client.current_timestamp == TS_BASE + 4 * 60_000

    def test_progress_tracking(self) -> None:
        klines = _make_klines(10)
        client = _make_client_with_klines(klines)

        event_loop = BacktestEventLoop(config=_make_config())
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1m'])
        event_loop.start()

        assert event_loop.is_completed is True
        assert event_loop.progress == 1.0

    def test_end_date_limits_replay(self) -> None:
        """end_date should stop the replay before all klines are processed"""
        # Create klines spanning 10 days (1d interval) so end_date can truncate
        daily_klines = [
            Kline(
                symbol=SYMBOL, timeframe='1d',
                open=2000.0 + i, high=2010.0 + i,
                low=1990.0 + i, close=2005.0 + i,
                volume=100.0,
                timestamp=TS_BASE + i * 86_400_000,  # 1 day in ms
                finished=True,
            )
            for i in range(10)
        ]
        # TS_BASE = 2023-11-14 22:13:20 UTC
        # Daily klines: day 0=Nov14, day 1=Nov15, day 2=Nov16, ...
        # end_date='2023-11-17' → midnight Nov17 UTC = 1700169600000
        # Klines before Nov17: day 0 (Nov14), day 1 (Nov15), day 2 (Nov16)
        # Day 3 starts at TS_BASE + 3*86400000 which is Nov17 → excluded
        config = BacktestConfig(
            symbol=SYMBOL, timeframe='1d',
            start_date='2023-11-14',
            end_date='2023-11-17',
        )
        strategy = CollectorStrategy(symbols=[SYMBOL], timeframes=['1d'])
        handler = KlineHandler(strategy)
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
        client._store_klines(SYMBOL, '1d', daily_klines)

        event_loop = BacktestEventLoop(config=config)
        event_loop.set_backtest_client(client)
        event_loop.subscribe(symbols=[SYMBOL], timeframes=['1d'])
        event_loop.add_handler(handler)
        event_loop.start()

        # Only klines before Nov17 should replay (day 0, 1, 2)
        assert len(strategy.received_klines) == 3

    def test_fetch_ohlcv_limit_zero_returns_all(self) -> None:
        klines = _make_klines(200)
        client = _make_client_with_klines(klines)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=0)
        assert len(result) == 200
```

- [ ] **Step 2: Update test_backtest_runner.py**

Replace `BacktestEventLoop(start_index=0)` constructions with the new interface. Since `BacktestRunner` now passes `config` internally, the runner tests mostly work via `BacktestRunner(config)`.

Update `TestBacktestEventLoopSubscribe` — each `BacktestEventLoop(start_index=0)` becomes:

```python
el = BacktestEventLoop(config=BacktestConfig(
    symbol=SYMBOL, timeframe='1m',
    start_date='2025-01-01', end_date='2025-02-01',
))
```

Remove `start_index=0` from all `BacktestConfig` constructions in runner tests (field removed in Task 4).

- [ ] **Step 3: Run all backtest tests**

Run: `uv run pytest tests/test_backtest_event_loop.py tests/test_backtest_runner.py tests/test_backtest_client.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_backtest_event_loop.py tests/test_backtest_runner.py
git commit -m "test: update tests for BacktestEventLoop config-based constructor"
```

### Task 7: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 2: Run mypy type check**

Run: `uv run mypy backtest/`
Expected: No errors

- [ ] **Step 3: Verify git diff is clean and complete**

Run: `git diff dev_1.1...HEAD`
Review that all changes are intentional and no debug code remains.
