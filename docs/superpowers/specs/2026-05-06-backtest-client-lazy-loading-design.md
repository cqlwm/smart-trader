# BacktestClient 懒加载重构

## 目标

将 BacktestClient 从"构造时加载所有数据"改为"fetch_ohlcv 调用时懒加载"，使其行为与真实交易所客户端一致。

## 变更

### 1. 移除构造器参数

从 `BacktestClient.__init__` 中移除：

- `symbol: Symbol | None`
- `timeframe: str | None`
- `start_date: str | None`
- `end_date: str | None`
- `extra_timeframes: tuple[str, ...]`

构造器仅保留：

```python
def __init__(
    self,
    data_store: KlineDataStore,
    order_repo: OrderRepository,
    initial_balance: float = 10000.0,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0004,
    symbol_infos: dict[str, SymbolInfo] | None = None,
) -> None:
```

### 2. 删除 `_load_data_from_store`

该方法整体移除，数据加载职责移入 `fetch_ohlcv`。

### 3. fetch_ohlcv 懒加载

当缓存中无 `symbol+timeframe` 数据时，通过 `current_timestamp` 和 `limit` 推算日期范围，从 `KlineDataStore` 加载并缓存：

1. `end_date` = `current_timestamp` 转日期
2. `start_date` = `end_date - (limit × timeframe_ms)`，再加 30% buffer
3. `data_store.ensure_data()` → `data_store.load_csv()` → 存入 `_kline_cache`
4. 后续请求直接返回缓存

### 4. BacktestRunner 适配

`_create_backtest_client` 不再传递数据相关参数。

### 5. BacktestEventLoop 适配

`_load_subscribed_klines` 中 `fetch_ohlcv(symbol, tf, start_time=..., end_time=..., limit=0)` 的调用无需修改——懒加载机制自动处理首次数据获取。

## 不变更

- `ExSwapClient` 接口不变
- `fetch_ohlcv` 的签名不变
- `KlineDataStore` 接口不变
- `OrderRepository` 接口不变
- `BacktestConfig` 结构不变（Runner 仍从 config 获取日期范围，只是不再传给 Client）
