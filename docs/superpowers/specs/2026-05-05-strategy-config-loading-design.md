# Strategy Config Loading Design

## Problem

`BotManager.start_bot()` 硬编码 `from template import dogeusdc` / `btcusdc_smc`，新增策略必须修改代码。策略参数（per_order_qty、grid_spacing_rate 等）也散落在代码中，无法通过配置调整。

## Goal

- 通过 YAML 配置文件完整定义策略实例（类型、参数、信号）
- 新增策略实例只需编辑 YAML，零代码改动
- 新增策略类型只需注册 `(StrategyClass, ConfigClass)` 对
- 支持运行时通过 API 动态增删策略实例
- 删除 template 目录，`order_file_path` 由 OrderRepository 接管

## Architecture

```
┌──────────────┐     ┌────────────────┐     ┌──────────────────────┐
│ strategies.  │     │ StrategyLoader  │     │ StrategyInstanceMgr  │
│   yaml       │────▶│ (读取+验证+构建) │────▶│ (已有，管理生命周期)    │
└──────────────┘     └────────────────┘     └──────────────────────┘
                            │                        │
                            ▼                        ▼
                   ┌────────────────┐        ┌──────────────┐
                   │ StrategyRegistry│        │ BotManager    │
                   │ (增强)          │        │ (简化)        │
                   └────────────────┘        └──────────────┘
                            ▲
                   ┌────────┴────────┐
                   │ 策略类 (自注册)   │
                   │ @register_strategy│
                   └─────────────────┘
```

## YAML Format

```yaml
strategies:
  - type: signal_grid
    config:
      symbol: { base: doge, quote: usdc }
      timeframe: 5m
      position_side: LONG
      master_side: BUY
      per_order_qty: 3500
      grid_spacing_rate: -0.1
      max_order: 3
      enable_exit_signal: true
      signal:
        type: alpha_trend_grids
        inner:
          type: alpha_trend
          side: BUY
      exit_signal_take_profit_min_rate: 0.03
      fixed_rate_take_profit: true
      fixed_take_profit_rate: 0.03
      enable_max_order_stop_loss: true
      position_reverse: false

  - type: signal_grid
    config:
      symbol: { base: doge, quote: usdc }
      timeframe: 5m
      position_side: SHORT
      master_side: SELL
      per_order_qty: 200
      grid_spacing_rate: 0.0001
      max_order: 10
      enable_exit_signal: true
      signal:
        type: alpha_trend_grids
        inner:
          type: alpha_trend
          side: BUY
      exit_signal_take_profit_min_rate: 0.005
      fixed_rate_take_profit: true
      fixed_take_profit_rate: 0.01
      enable_max_order_stop_loss: true
      position_reverse: true

  - type: smc_intraday
    config:
      symbol: { base: btc, quote: usdc }
      timeframes: [1w, 1d, 5m]
      risk_per_trade_pct: 1.0
      account_balance: 100.0
```

- `type` 对应 StrategyRegistry 中注册的策略类型名
- `config` 字段直接映射到 Config 类构造参数
- `signal` 使用嵌套 `type` 字段通过 Signal Registry 解析
- `order_file_path` 不在配置中出现，由 OrderRepository 接管
- `market_trend` 拆为独立的 long/short 实例，判断逻辑由人在 YAML 中选配

## Core Components

### SignalRegistry

```python
# strategy/signal_registry.py
class SignalRegistry:
    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, signal_class: type) -> None: ...

    @classmethod
    def get(cls, name: str) -> type: ...

    @classmethod
    def from_config(cls, config: dict) -> Signal:
        """递归解析嵌套 signal 配置，构建信号对象"""
        cfg = {**config}
        signal_type = cfg.pop("type")
        signal_cls = cls.get(signal_type)
        if "inner" in cfg:
            cfg["inner"] = cls.from_config(cfg.pop("inner"))
        return signal_cls(**cfg)

def register_signal(name: str):
    """装饰器，信号类自注册"""
    ...
```

注册示例：

```python
@register_signal("alpha_trend")
class AlphaTrendSignal:
    def __init__(self, side: OrderSide): ...

@register_signal("alpha_trend_grids")
class AlphaTrendGridsSignal:
    def __init__(self, inner: AlphaTrendSignal): ...
```

### StrategyRegistry (Enhanced)

`StrategyRegistry` 注册 `(strategy_class, config_class)` 对：

```python
# strategy/registry.py
class StrategyRegistry:
    _registry: dict[str, tuple[type, type]] = {}

    @classmethod
    def register(cls, name: str, strategy_class: type, config_class: type) -> None: ...

    @classmethod
    def get(cls, name: str) -> tuple[type, type]:
        """返回 (strategy_class, config_class)"""
        ...

def register_strategy(name: str, config_class: type):
    """装饰器，绑定策略类与配置类"""
    def decorator(cls: type) -> type:
        StrategyRegistry.register(name, cls, config_class)
        return cls
    return decorator
```

### StrategyLoader

```python
# strategy/loader.py
class StrategyLoader:
    def __init__(self, config_path: str) -> None:
        self._config_path = config_path

    def load(self, ex_client: ExSwapClient) -> list[KlineHandler]:
        """读取 YAML，构建 KlineHandler 列表"""
        raw = self._read_yaml()
        handlers: list[KlineHandler] = []
        for entry in raw.get("strategies", []):
            strategy_cls, config_cls = StrategyRegistry.get(entry["type"])
            config = self._build_config(config_cls, entry["config"])
            strategy = strategy_cls(config, ex_client)
            handlers.append(KlineHandler(strategy))
        return handlers

    def _build_config(self, config_cls: type, raw: dict) -> object:
        """将 YAML dict 转为 Config 实例，处理特殊字段转换"""
        ...

    # _build_config 需处理的特殊字段：
    # - symbol: dict -> Symbol(base=..., quote=...)
    # - signal: dict -> SignalRegistry.from_config(...)
    # - position_side / master_side / side: str -> 枚举值 (PositionSide/OrderSide)
    # 其他字段原样传递
```

### BotManager Simplification

```python
class BotManager:
    def __init__(self, ex_client, el, config_path: str) -> None:
        ...
        self._config_path = config_path

    def start_bot(self) -> None:
        loader = StrategyLoader(self._config_path)
        handlers = loader.load(self.ex_client)
        # ... 订阅 kline + 添加 handler + 启动（逻辑不变）
```

### Strategy Registration

```python
# strategy/strategies/signal_grid.py
@register_strategy("signal_grid", SignalGridStrategyConfig)
class SignalGridStrategy:
    ...

# strategy/strategies/smc_intraday.py
@register_strategy("smc_intraday", SMCIntradayConfig)
class SMCIntradayStrategy:
    ...
```

### Module Auto-Discovery

`strategy/strategies/__init__.py` import 所有策略模块触发注册：

```python
from strategy.strategies import signal_grid, smc_intraday
```

BotManager 启动前 import 该模块确保注册完成。

## Error Handling

| 场景 | 行为 |
|------|------|
| YAML 文件不存在 | CRITICAL 日志 + 抛出 FileNotFoundError，阻止启动 |
| 策略 type 未注册 | 跳过该条目，ERROR 日志列出可用类型，其余策略正常启动 |
| Signal type 未注册 | 跳过该策略实例，ERROR 日志 |
| Config 参数错误（缺字段、类型不匹配） | 跳过该实例，ERROR 日志包含字段和原因 |
| 运行时 API 新增策略验证失败 | 返回明确错误信息，不影响已运行策略 |

原则：单条策略配置错误不影响其他策略启动，所有错误必须记录日志。

## Testing

### Unit Tests

- `SignalRegistry.from_config` 正确解析嵌套 signal 配置
- `StrategyLoader._build_config` 将 YAML dict 转为 Config 实例
- `StrategyLoader.load` 跳过无效条目，不抛出异常
- `StrategyRegistry.register` / `get` 正常工作和重复注册覆盖行为

### Integration Tests

- 从 YAML 文件完整构建 KlineHandler 列表
- BotManager 使用 StrategyLoader 启动，订阅正确的 kline streams
- 运行时动态新增/删除策略实例

### Edge Cases

- 空 strategies 列表
- 配置文件不存在
- signal 嵌套层级（1 层 vs 2 层）

## Runtime Dynamic Management

运行时通过 API 增删策略实例时，`StrategyInstanceManager` 调用 `StrategyLoader._build_config` 验证和构建配置。流程：

1. API 收到新增策略请求（type + config dict）
2. `StrategyInstanceManager.create()` 记录实例
3. `StrategyInstanceManager.start()` 调用 `StrategyLoader._build_config` 构建配置，创建策略和 KlineHandler
4. 将 handler 加入已运行的 DataEventLoop
5. 删除策略时，从 DataEventLoop 移除对应 handler

BotManager 需要提供方法让 StrategyInstanceManager 访问 DataEventLoop 进行 handler 的增删。

## Scope

- 删除 `template/` 目录
- 从 `SignalGridStrategyConfig` 中移除 `order_file_path`
- `market_trend` 拆为独立的 long/short 策略实例
