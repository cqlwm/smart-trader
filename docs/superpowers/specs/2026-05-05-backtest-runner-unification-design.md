# BacktestRunner 统一入口设计

## 目标

将回测入口从独立的 `run_backtest.py` 统一到 `run.py`，通过 `BacktestRunner` 封装类隔离回测特有逻辑，复用 `BotManager` 的策略加载和事件循环管理。

## 现状

- `run.py`：实盘入口，创建 `BinanceSwapClient` + `BinanceDataEventLoop` → `BotManager.start_bot()`
- `run_backtest.py`：回测入口，手动创建 `BacktestClient` + `BacktestEventLoop` + `KlineHandler`，绕过 `BotManager`
- `BotManager`：接受 `ExSwapClient` + `DataEventLoop` 依赖注入，内部负责策略加载和启动事件循环

## 问题

- `run_backtest.py` 重复了 `BotManager` 的策略加载逻辑（手动创建 `KlineHandler`）
- 两个入口文件造成维护负担
- `run_backtest.py` 中硬编码策略配置，无法复用 YAML 配置

## 设计

### BacktestRunner

位置：`backtest/backtest_runner.py`

```python
@dataclass(frozen=True)
class BacktestConfig:
    # 策略配置复用 YAML 文件，通过 BotManager → StrategyLoader 加载
    config_path: str = "strategies.yaml"
    symbol: Symbol  # 主交易对，用于数据加载
    timeframe: str  # 主K线周期，用于数据加载
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
    data_dir: str = "data"
    start_index: int = 300

class BacktestRunner:
    def __init__(self, config: BacktestConfig) -> None:
        # 1. 创建 BacktestClient（含 KlineDataStore 和数据加载）
        # 2. 创建 BacktestEventLoop（含进度回调）
        # 3. 关联 BacktestClient → BacktestEventLoop
        # 4. 创建 BotManager(ex_client=backtest_client, el=backtest_event_loop)

    def run(self) -> dict[str, Any]:
        # 1. BotManager.start_bot()
        # 2. TradeAnalysis 分析
        # 3. 返回分析结果

    def report(self) -> str:
        # 生成文本报告并保存到文件
```

### run.py 统一入口

```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["api", "no-api", "backtest"], default="api")
    parser.add_argument("--config", default="strategies.yaml")
    # 回测相关参数
    # 策略通过 YAML 配置加载，无需 --strategy 参数
    parser.add_argument("--symbol", help="Primary trading symbol (e.g. DOGE/USDT)")
    parser.add_argument("--timeframe", help="Primary kline timeframe (e.g. 1m)")
    parser.add_argument("--start", help="Backtest start date")
    parser.add_argument("--end", help="Backtest end date")
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    if args.mode == "backtest":
        config = BacktestConfig(...)
        runner = BacktestRunner(config)
        result = runner.run()
        print(runner.report())
    elif args.mode == "no-api":
        BotManager(ex_client=create_binance_client("MAIN"), el=BinanceDataEventLoop(), config_path=args.config).start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
```

### 删除文件

- `run_backtest.py`：完全删除，其中硬编码的策略示例不再需要

### 不修改

- `BotManager`：零修改
- `BacktestClient`：零修改
- `BacktestEventLoop`：零修改
- `TradeAnalysis`：零修改
- `BacktestConfig`（现有）：可能需要微调字段以匹配命令行参数需求

## 数据流

```
run.py --mode backtest
  → BacktestRunner(config)
    → BacktestClient（数据加载、模拟交易）
    → BacktestEventLoop（历史 K 线重放）
    → BacktestEventLoop.set_backtest_client(backtest_client)
    → BotManager(ex_client=backtest_client, el=backtest_event_loop)
    → BotManager.start_bot()
      → StrategyLoader 加载策略
      → BacktestEventLoop.start()（同步阻塞直到回测完成）
    → TradeAnalysis(backtest_client).analyze()
  → 返回分析结果 + 生成报告
```
