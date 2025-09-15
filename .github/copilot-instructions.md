# Smart Trader AI Agent Instructions

本文档为AI代理提供关于smart-trader项目的关键信息和开发指南。

## 项目概述

Smart Trader是一个加密货币自动交易系统,主要功能包括:
- 实时K线数据订阅和处理
- 多策略支持(网格交易、趋势跟踪等)
- 多交易所API集成(Binance、Bybit、OKX等)
- 事件驱动架构设计

## 核心架构

### 事件循环系统
- `DataEventLoop.py`: 核心事件处理系统
  - 使用WebSocket订阅交易所数据
  - 支持多任务并发处理
  - Task基类定义了数据处理接口

### 交易所集成
- `client/ex_client.py`: 统一交易所接口抽象
- 实现类:
  - `binance_client.py`
  - `bybit_client.py` 
  - `okx_client.py`

### 策略系统
- `strategy/`: 存放所有交易策略
- 每个策略都需要实现`StrategyV2`接口
- 主要策略类型:
  - 网格策略
  - 趋势信号策略

## 开发规范

1. 依赖管理:
   ```bash
   uv venv  # 创建虚拟环境
   uv pip install -r requirements.txt
   ```

2. 代码风格:
   - 所有函数必须包含类型注解
   - 保持简单的调用链,避免过度抽象
   - 仅在复杂逻辑处添加注释
   - 使用测试驱动开发(TDD)方法

3. 测试规范:
   - 新功能必须编写对应的单元测试
   - 测试文件位于`test/`目录
   - 运行测试: `python -m pytest`

## 关键工作流

1. 添加新策略:
   ```python
   from strategy import StrategyV2
   
   class MyStrategy(StrategyV2):
       def run(self, kline: Kline):
           # 实现策略逻辑
   ```

2. 集成新交易所:
   ```python
   from client.ex_client import ExClient
   
   class NewExchangeClient(ExClient):
       # 实现抽象方法
   ```

3. 启动交易:
   - 配置环境变量(API密钥等)
   - 修改`strategies.json`定义策略参数
   - 运行: `python run.py`

## 项目特有模式

1. 事件处理:
   - 使用Task子类处理特定类型的事件
   - 在DataEventLoop中注册任务
   
2. 订单管理:
   - 使用custom_id跟踪订单状态
   - 实现ChaserOrder模式处理订单更新

3. 信号系统:
   - Alpha趋势信号生成和处理
   - 网格策略与信号系统的集成

## 常见问题解决

1. WebSocket连接断开:
   - 实现了自动重连机制
   - 检查日志中的错误信息

2. 订单执行延迟:
   - 使用ChaserOrder机制优化订单执行
   - 调整策略参数降低时间敏感度

## 环境配置

必需的环境变量:
```
BINANCE_API_KEY=你的密钥
BINANCE_API_SECRET=你的密钥
BINANCE_IS_TEST=True/False
```

建议使用`strategies_dev.json`进行策略测试。