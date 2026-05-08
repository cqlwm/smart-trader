# 移除 SMC 多时间框架代码设计

## 目标

移除 SMC 模块中所有多时间框架（MTF）相关代码，仅保留单一时间框架分析能力。

## 决策背景

- SMC 的核心价值在单时间框架的结构分析（BOS/CHOCH、OB、FVG 等）
- MTF 逻辑增加了复杂度但未带来对应收益
- SMCEngine 已是纯分析器（接收 df，输出 SMCResult），不依赖时间框架

## 删除文件

| 文件 | 原因 |
|------|------|
| `strategies/smc/mtf.py` | MTF 分析器核心 |
| `strategies/smc/strategy/` 整个目录 | Intraday/Simple/Conditions/Risk 均依赖 MTF |
| `strategy/smc_signal/smc_intraday_strategy.py` | MTF 运行时策略 |

## 修改文件

| 文件 | 变更 |
|------|------|
| `strategies/smc/__init__.py` | 移除 MTF 相关导出 |
| `strategies/smc/schemas.py` | 删除 MTF 相关 schema 类，保留信号 + 单周期输出 |
| `strategy/smc_signal/__init__.py` | 移除 SMCIntradayStrategy 导出 |
| `api/routes/backtest.py` | 移除 smc_intraday 策略工厂 + MTF 特殊处理 |
| 其他引用 MTF 的文件 | 清理残留导入和引用 |

## 保留不动

| 文件/目录 | 原因 |
|-----------|------|
| `strategies/smc/engine.py` | 单周期核心引擎 |
| `strategies/smc/config.py` | SMCConfig 仅含单周期配置 |
| `strategies/smc/types.py` | 基础数据类型 |
| `strategies/smc/core/` | 底层指标检测 |
| `strategies/smc/indicators/` | ATR 等指标 |
| `strategies/smc/signal.py` | 单周期信号生成 |
| `strategies/smc/output.py` | 单周期输出构建 |
| `strategy/smc_signal/smc_signal.py` | 单周期信号适配器 |

## 验证

- `from strategies.smc import SMCEngine, SMCConfig` 导入正常
- `mypy` 类型检查通过
- 无残留 MTF/IntradayStrategy 引用
