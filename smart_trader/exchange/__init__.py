"""交易所抽象层

外部统一通过本包与交易所交互：
- `exchange.ports`：端口（ABC）与 `Exchange` 组合接口, 零第三方依赖, 业务层唯一入口;
- `exchange.binance`：Binance 适配器, ccxt / REST 细节只存在于其中。

依赖规则见 .memory/01-system-architecture.md 第一节: 业务层不得 import ccxt,
raw 交易所实例不得越过适配器边界。
"""
