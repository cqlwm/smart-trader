# 币安 BNB 币本位合约交易脚本开发计划

## 1. 概述
编写一个独立的 Python 脚本，使用 `ccxt` 库连接币安（Binance）交易所，针对 BNB 币本位合约（Coin-Margined Futures）执行基于昨日日线涨跌趋势的自动交易策略。

## 2. 依赖与环境
- 依赖项：使用 `uv` 确保安装 `ccxt`。
- 环境变量：依赖 `BINANCE_API_KEY` 和 `BINANCE_SECRET_KEY` 用于身份认证。

## 3. 核心功能设计
脚本将包含以下几个核心模块，严格遵守类型注解和异常处理规范：

### 3.1 客户端初始化模块
- 实例化 `ccxt.binance` 客户端。
- 设置 `options={'defaultType': 'delivery'}` 以支持币安币本位（Coin-M）合约市场。
- 设定交易标的，通常在 ccxt 中表示为 `BNB/USD:BNB`（具体以 ccxt 规则为准）。

### 3.2 趋势判断模块
- **逻辑**：获取日线（`1d`）级别的 OHLCV 数据。
- **判断标准**：取昨日的 K 线（最近完整的一天），比较收盘价（Close）与开盘价（Open）。
  - 下跌：Close < Open
  - 上涨：Close >= Open

### 3.3 仓位状态检查模块
- 获取账户当前的合约持仓（`fetch_positions`）。
- 过滤出 `BNB` 币本位合约的多头（LONG）和空头（SHORT）仓位。
- 提取持仓数量（contracts/amount）和未实现盈亏（unrealizedProfit），判断对应方向仓位是否处于盈利状态。

### 3.4 交易执行模块（基于双向持仓 Hedge Mode）
根据趋势和仓位状态执行对应的市价单（Market Order）：
- **若昨天下跌**：
  1. 检查空头（SHORT）仓位：如果持有且盈利（unrealized PnL > 0），执行**平空** 1 张合约（买入，平空）。
  2. 否则（空头不盈利或无空头仓位）：执行**开多** 1 张合约（买入，开多）。
- **若昨天上涨**：
  1. 检查多头（LONG）仓位：如果持有且盈利（unrealized PnL > 0），执行**平多** 1 张合约（卖出，平多）。
  2. 否则（多头不盈利或无多头仓位）：执行**开空** 1 张合约（卖出，开空）。

### 3.5 日志与异常处理
- 使用 Python 标准库 `logging` 记录关键步骤（INFO 级别记录行情与下单结果，ERROR 级别记录异常）。
- 精准捕获 `ccxt.NetworkError`、`ccxt.ExchangeError` 等异常，避免静默失败。

## 4. 实施步骤
1. **创建脚本文件**：在合适的位置（例如 `scripts/bnb_coin_m_strategy.py`）创建独立脚本。
2. **编写辅助函数**：实现行情获取、仓位查询等原子操作（函数控制在50行以内）。
3. **编写主业务逻辑**：组装策略判断与下单动作。
4. **规范与检查**：检查并完善所有函数的类型注解（Type Hints），并符合 Python 规范要求。
