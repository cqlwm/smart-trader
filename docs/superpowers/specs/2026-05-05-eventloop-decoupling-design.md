# BotManager EventLoop Decoupling Design

## Problem

`BotManager.start_bot()` 直接 `new BinanceDataEventLoop(kline_subscribes)` 并使用 `symbol.binance_ws_sub_kline()` 构建订阅 key，导致 BotManager 与 Binance 实现耦合。BotManager 本应只关心"我要订阅哪些 symbol/timeframe"，不应知道数据从 Binance WebSocket 获取。

## Goal

- BotManager 不再 import `BinanceDataEventLoop`
- BotManager 只与 `DataEventLoop` 基类交互
- 订阅逻辑封装在具体 EventLoop 实现中
- EventLoop 由外部注入，BotManager 不负责创建

## Design

### DataEventLoop 基类增强

在 `DataEventLoop` 中增加 `subscribe` / `unsubscribe` 方法（默认 no-op）：

```python
class DataEventLoop:
    def __init__(self) -> None:
        self.handlers: list[Handler] = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    def add_handler(self, handler: Handler) -> None:
        self.handlers.append(handler)

    def subscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        pass

    def unsubscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        pass

    def loop(self, event: Event) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

默认 no-op 确保 `ScheduledEventLoop` 等不需要订阅的子类不受影响。

### BinanceDataEventLoop 实现

Override `subscribe` / `unsubscribe`，内部处理 `binance_ws_sub_kline` 格式转换：

```python
class BinanceDataEventLoop(DataEventLoop):
    def subscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        for symbol in symbols:
            for timeframe in timeframes:
                key = symbol.binance_ws_sub_kline(timeframe)
                self.add_kline_subscribe(key)

    def unsubscribe(self, symbols: list[Symbol], timeframes: list[str]) -> None:
        for symbol in symbols:
            for timeframe in timeframes:
                key = symbol.binance_ws_sub_kline(timeframe)
                self.remove_kline_subscribe(key)
```

构造函数不再需要 `kline_subscribes` 参数——订阅由 `subscribe()` 动态管理。

### BotManager 简化

```python
class BotManager:
    def __init__(self, ex_client: ExSwapClient, el: DataEventLoop, config_path: str = "strategies.yaml") -> None:
        self.ex_client = ex_client
        self.data_event_loop = el
        self._config_path = config_path
        ...

    def start_bot(self) -> None:
        loader = StrategyLoader(self._config_path)
        handlers = loader.load(self.ex_client)

        for handler in handlers:
            self.data_event_loop.add_handler(handler)
            self.data_event_loop.subscribe(handler.strategy.symbols, handler.strategy.timeframes)

        self.data_event_loop.start()
```

关键变化：
- 不再 import `BinanceDataEventLoop`
- 不再自己 `new BinanceDataEventLoop(kline_subscribes)`
- 不再知道 `binance_ws_sub_kline` 格式
- EventLoop 完全由外部注入

### run.py 调整

调用方负责创建具体的 EventLoop：

```python
BotManager(
    ex_client=create_binance_client("MAIN"),
    el=BinanceDataEventLoop(),
    config_path=config_path,
).start_bot()
```

## Files

| Action | Path | Change |
|--------|------|--------|
| Modify | `event_loop/base.py` | 增加 `subscribe`/`unsubscribe` 方法 |
| Modify | `event_loop/binance.py` | Override `subscribe`/`unsubscribe`，移除构造函数 `kline_subscribes` 参数 |
| Modify | `bot_manager.py` | 移除 BinanceDataEventLoop import，使用 DataEventLoop 接口 |
| Modify | `run.py` | 创建 `BinanceDataEventLoop()` 传入 BotManager |
| Modify | `test/test_event_layer.py` | 适配新的 BinanceDataEventLoop 构造函数 |
| Modify | `backtest/` 相关文件 | 适配新的 BinanceDataEventLoop 构造函数 |

## Testing

- `DataEventLoop.subscribe` / `unsubscribe` 默认 no-op
- `BinanceDataEventLoop.subscribe` 正确转换为 `binance_ws_sub_kline` 并调用 `add_kline_subscribe`
- `BinanceDataEventLoop.unsubscribe` 正确转换并调用 `remove_kline_subscribe`
- BotManager 不再 import BinanceDataEventLoop
- 现有测试通过
