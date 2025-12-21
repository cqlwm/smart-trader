# 回测系统使用指南

本项目新增了完整的策略回测功能，可以使用历史数据对交易策略进行回测分析。

## 功能特性

- **真实模拟**: 使用历史K线数据重放，模拟真实交易环境
- **完整交易逻辑**: 支持市价单、限价单，包含交易手续费计算
- **多种指标分析**: 收益、胜率、最大回撤、夏普比率等专业指标
- **详细报告**: 自动生成回测报告和图表分析
- **灵活配置**: 支持多种数据格式和时间框架

## 快速开始

### 1. 准备历史数据

需要CSV格式的历史数据文件，包含以下列：
- `timestamp`: 时间戳（毫秒）
- `open`: 开盘价
- `high`: 最高价
- `low`: 最低价
- `close`: 收盘价
- `volume`: 交易量

示例：
```csv
timestamp,open,high,low,close,volume
1672531200000,16500.0,16600.0,16400.0,16550.0,1234.56
1672534800000,16550.0,16700.0,16500.0,16680.0,2345.67
```

### 2. 创建示例数据（可选）

如果没有真实数据，可以先生成示例数据：

```bash
python run_backtest.py --create-sample
```

这会在 `data/btcusdt_1h_sample.csv` 生成一年的模拟数据。

### 3. 运行回测

```bash
# 使用默认数据文件
python run_backtest.py

# 指定自定义数据文件
python run_backtest.py --data-file path/to/your/data.csv
```

## 回测结果说明

回测完成后会显示以下关键指标：

### 收益指标
- **总收益率**: 整个回测期间的总收益百分比
- **年化收益率**: 按年计算的复利收益率
- **净收益**: 扣除手续费后的实际收益

### 风险指标
- **最大回撤**: 最高点到最低点的最大跌幅
- **波动率**: 收益的波动程度
- **夏普比率**: 风险调整后的收益指标

### 交易指标
- **胜率**: 盈利交易占总交易的比例
- **利润因子**: 盈利总额与亏损总额的比率
- **平均交易收益**: 每次交易的平均收益

## 架构说明

### 核心组件

1. **BacktestClient** (`backtest/backtest_client.py`)
   - 模拟交易所API
   - 处理订单、成交、余额管理
   - 支持市价单和限价单

2. **HistoricalDataLoader** (`backtest/data_loader.py`)
   - 加载历史K线数据
   - 支持CSV和JSON格式
   - 数据缓存和过滤功能

3. **BacktestEventLoop** (`backtest/backtest_event_loop.py`)
   - 控制回测时间流
   - 重放历史K线数据
   - 支持暂停、加速、单步执行

4. **BacktestTask** (`task/backtest_task.py`)
   - 包装策略执行
   - 收集交易结果

5. **BacktestAnalyzer** (`backtest/analyzer.py`)
   - 计算各项指标
   - 生成分析报告

## 自定义策略回测

要回测自己的策略：

1. 确保策略继承 `SingleTimeframeStrategy` 或 `MultiTimeframeStrategy`
2. 在 `run_backtest.py` 中修改策略配置
3. 运行回测脚本

示例代码：

```python
from strategy.your_strategy import YourStrategy, YourStrategyConfig

# 配置策略参数
config = YourStrategyConfig(
    symbol=symbol,
    timeframe=timeframe,
    # 其他参数...
)

# 创建策略实例
strategy = YourStrategy(config, backtest_client)
```

## 配置参数

### BacktestClient 参数
- `initial_balance`: 初始资金（默认10000.0）
- `maker_fee`: 挂单手续费率（默认0.0002）
- `taker_fee`: 吃单手续费率（默认0.0004）

### BacktestEventLoop 参数
- `speed_multiplier`: 回放速度倍数（0为手动步进）
- `on_progress_callback`: 进度回调函数

## 数据格式支持

### CSV格式
```csv
timestamp,open,high,low,close,volume
1672531200000,16500.0,16600.0,16400.0,16550.0,1234.56
```

### JSON格式
```json
[
  {
    "timestamp": 1672531200000,
    "open": 16500.0,
    "high": 16600.0,
    "low": 16400.0,
    "close": 16550.0,
    "volume": 1234.56
  }
]
```

## 注意事项

1. **数据质量**: 确保历史数据的准确性和完整性
2. **手续费设置**: 根据实际交易所设置合适的手续费率
3. **内存使用**: 大量历史数据可能消耗较多内存
4. **时间对齐**: 确保K线数据按时间顺序排列
5. **滑点考虑**: 当前版本未包含滑点，实际交易可能有差异

## 扩展功能

未来可以添加的功能：
- 多策略并行回测
- 参数优化（网格搜索）
- 滑点模拟
- 成交概率模型
- 更详细的图表分析

## 故障排除

### 常见问题

1. **数据加载失败**
   - 检查文件路径和格式
   - 确认列名正确

2. **策略不交易**
   - 检查信号逻辑
   - 确认价格数据更新

3. **结果异常**
   - 检查手续费设置
   - 验证数据时间顺序

4. **内存不足**
   - 减少数据量
   - 使用数据过滤

如有问题，请检查日志输出或查看代码注释。
