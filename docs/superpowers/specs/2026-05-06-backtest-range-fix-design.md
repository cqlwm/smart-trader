# BacktestEventLoop 回测范围修复

## 问题

BacktestEventLoop 存在两个 bug：

1. **`_load_subscribed_klines` 调用 `fetch_ohlcv` 不传时间参数** — `fetch_ohlcv(symbol, tf)` 在 `current_timestamp=0` 时走 timestamp 过滤分支，返回空列表
2. **缺少 end 边界** — `_run_backtest_sync` 循环到 `len(historical_klines)`，无法在 `end_date` 对应的时间点提前截止

根因：`BacktestConfig` 已有 `start_date`/`end_date` 定义回测范围，但 `BacktestEventLoop` 没有使用它们。

## 设计

**核心思路**: `BacktestConfig.start_date` / `end_date` 是回测的唯一时间范围，`BacktestEventLoop` 利用 config 来限制 K 线加载范围和循环终止条件。

### 改动 1: BacktestEventLoop 接收 BacktestConfig

```python
class BacktestEventLoop(DataEventLoop):
    def __init__(self, config: BacktestConfig, on_progress_callback: ...) -> None:
        self._config = config
        self._start_ts = _parse_date_to_timestamp(config.start_date)
        self._end_ts = _parse_date_to_timestamp(config.end_date)
```

移除 `start_timestamp` 和 `start_index` 参数，改由 config 统一管理。

### 改动 2: _load_subscribed_klines 传入时间范围

```python
def _load_subscribed_klines(self) -> list[Kline]:
    for key, (symbol, tf) in self._subscriptions:
        klines = self.backtest_client.fetch_ohlcv(
            symbol, tf,
            start_time=self._start_ts,
            end_time=self._end_ts,
        )
        collected.extend(klines)
```

### 改动 3: _resolve_start_index 和 _resolve_end_index

`_resolve_start_index`: 使用 `self._start_ts`，回退到默认值（min(300, len-1)）保留预热逻辑。

`_resolve_end_index`（新增）: 根据 `self._end_ts` 找到最后一条 `timestamp < end_ts` 的 kline 索引 +1。默认 `len(historical_klines)`。

### 改动 4: _run_backtest_sync 循环条件

```python
while self.is_running and self.current_index < self.end_index:
```

### 改动 5: BacktestRunner 传 config

```python
self._event_loop = BacktestEventLoop(config=config)
```

移除 `start_index=config.start_index` 传参。

### 辅助函数

```python
def _parse_date_to_timestamp(date_str: str) -> int:
    """将 YYYY-MM-DD 格式转为 UTC 毫秒时间戳"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
```

## 影响范围

- `backtest/backtest_event_loop.py` — 主要改动
- `backtest/backtest_runner.py` — 传参调整
- `run.py` — 无变化（BacktestRunner 接口不变）
