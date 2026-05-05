# Backtest Refactoring Design

## Goal

Refactor the backtest module so that `ExClient.fetch_ohlcv` supports time-range parameters, enabling both live and backtest clients to fetch historical K-lines through a unified interface. A shared `KlineDataStore` replaces `HistoricalDataLoader`, and a new `BacktestAnalysis` class decouples analysis from `BacktestRunner`.

## Architecture

### Component Changes

```
Before:
  BacktestRunner → HistoricalDataLoader → CSV
  BacktestRunner → BacktestClient.load_historical_data()
  BacktestClient.fetch_ohlcv() reads from internal historical_data
  BacktestRunner → BacktestAnalyzer (direct call)

After:
  KlineDataStore (renamed from HistoricalDataLoader)
  ExClient.fetch_ohlcv(symbol, timeframe, limit, start_time, end_time)
  BinanceSwapClient.fetch_ohlcv → KlineDataStore cache or ccxt
  BacktestClient.fetch_ohlcv → in-memory historical_data filter
  BacktestAnalysis → wraps BacktestAnalyzer, reads from BacktestClient state
```

## 1. ExClient.fetch_ohlcv Signature Extension

**Current:**
```python
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100) -> list[Kline]
```

**New:**
```python
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]
```

- `start_time` / `end_time` are millisecond timestamps, optional
- Live clients (BinanceSwapClient, etc.): when provided, attempt cache first, fallback to ccxt
- BacktestClient: filter in-memory `historical_data` by time range
- No time parameters: behavior unchanged (current_timestamp-based for backtest, ccxt real-time for live)

## 2. KlineDataStore

Rename `HistoricalDataLoader` → `KlineDataStore`. Focus on data fetch + cache only.

**Retained methods:**
- `ensure_data(symbol, timeframe, start_time, end_time, data_dir, offset) -> str`
- `load_csv(file_path, symbol, timeframe) -> list[Kline]`
- `download_and_save_historical_data(symbol, interval, start_time, end_time, file_path) -> str`
- `clear_cache() -> None`

**Removed methods** (move to utility functions if still needed):
- `load_json` — unused in current flow
- `load_from_dataframe` — unused in current flow
- `filter_by_date_range` — filtering now handled in `fetch_ohlcv`
- `get_price_series` — analysis concern, not data storage

**Added:**
- `ExClient` base class gets `data_store: KlineDataStore | None` attribute (default `None`)
- `BinanceSwapClient.__init__` accepts optional `data_store` parameter

## 3. BinanceSwapClient.fetch_ohlcv

```python
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
    if start_time is not None or end_time is not None:
        if self.data_store:
            file_path = self.data_store.ensure_data(symbol, timeframe, start_time, end_time)
            klines = self.data_store.load_csv(file_path, symbol, timeframe)
            return self._filter_klines_by_time(klines, start_time, end_time, limit)
        return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit, start_time, end_time)
    return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit)
```

Key behaviors:
- `data_store` present + time params → cache-first
- `data_store` absent + time params → ccxt with `since` parameter
- No time params → existing ccxt real-time logic (unchanged)

## 4. BacktestClient.fetch_ohlcv

```python
def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
    key = f"{symbol.binance()}_{timeframe}"
    if key not in self.historical_data:
        return []

    klines = self.historical_data[key]

    if start_time is not None or end_time is not None:
        klines = [k for k in klines
                  if (start_time is None or k.timestamp >= start_time)
                  and (end_time is None or k.timestamp <= end_time)]
        return klines[-limit:] if len(klines) > limit else klines

    current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]
    return current_klines[-limit:] if len(current_klines) >= limit else current_klines
```

No real-time capability. Only reads from pre-loaded `historical_data`.

## 5. BacktestAnalysis

New class that wraps `BacktestAnalyzer` and reads state from `BacktestClient`:

```python
class BacktestAnalysis:
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

`BacktestAnalyzer` stays unchanged as a pure computation utility. `BacktestAnalysis` is the facade.

## 6. BacktestRunner Changes

- Replace `HistoricalDataLoader()` with `KlineDataStore()`
- Replace direct `BacktestAnalyzer` usage with `BacktestAnalysis`
- All other orchestration logic stays the same

## 7. Other Exchange Clients

- `BybitClient`, `OkxClient`, `MexcClient`: update `fetch_ohlcv` signature to accept `start_time/end_time`, ignore them initially (same as current BinanceSwapClient base behavior)
- These can implement cache-backed historical fetching later following the same pattern

## Files to Modify

| File | Change |
|------|--------|
| `backtest/data_loader.py` | Rename to `kline_data_store.py`, class rename, remove unused methods |
| `client/ex_client.py` | Add `data_store` attribute, update `fetch_ohlcv` signature |
| `client/binance_client.py` | Add `data_store` param, implement time-aware `fetch_ohlcv` |
| `client/bybit_client.py` | Update `fetch_ohlcv` signature |
| `client/okx_client.py` | Update `fetch_ohlcv` signature |
| `client/mexc_client.py` | Update `fetch_ohlcv` signature |
| `backtest/backtest_client.py` | Update `fetch_ohlcv` with time params; `load_historical_data` retained (still used by BacktestRunner to pre-load data) |
| `backtest/runner.py` | Use `KlineDataStore` and `BacktestAnalysis` |
| `backtest/backtest_analysis.py` | **New file** — `BacktestAnalysis` class |
| Tests | Update imports, add tests for new time-parameter behavior |
