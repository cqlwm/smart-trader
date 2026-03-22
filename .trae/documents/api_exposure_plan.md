# Smart-Trader API 暴露规划 (API Exposure Plan)

## 1. 目标与背景
当前项目是一个基于 Python 的量化交易机器人，核心事件循环 (`BinanceDataEventLoop`) 基于 `websocket-client` 库，是同步阻塞运行的。为了能够外部监控机器人状态、查看账户资产/仓位、以及动态管理策略，需要暴露一套 HTTP API，并保证不对原有的核心交易逻辑造成侵入或性能影响。

## 2. 技术栈选择
- **Web 框架**: `FastAPI` (原生支持异步，与项目现有的 Pydantic 深度集成，自动生成 OpenAPI 文档，项目 `pyproject.toml` 中已存在)。
- **Web 服务器**: `Uvicorn` (轻量级 ASGI 服务器，`pyproject.toml` 中已存在)。
- **并发模型**: 采用 **FastAPI 主线程 + 策略后台线程** 的架构。通过 FastAPI 的 `lifespan` (生命周期事件) 来在后台线程启动量化机器人，并在 FastAPI 停止时优雅关闭交易事件循环。
- **鉴权**: 简单的 `API-Key` 鉴权 (通过 HTTP Header `X-API-Key` 传递)，因涉及资金和仓位信息，必须保证接口调用安全。

## 3. 目录结构设计
遵循高内聚低耦合的原则，新增 `api` 模块：
```text
smart-trader/
├── api/
│   ├── __init__.py
│   ├── main.py           # FastAPI 实例、CORS、异常处理、Lifespan生命周期
│   ├── dependencies.py   # 依赖注入 (如 API-Key 鉴权、获取 Client/Bot 全局实例)
│   ├── routes/           # 路由分组
│   │   ├── __init__.py
│   │   ├── system.py     # 健康检查、系统状态
│   │   ├── account.py    # 资产、仓位查询
│   │   └── strategy.py   # 策略状态监控
│   └── schemas/          # Pydantic 响应/请求模型，统一接口数据结构
│       ├── __init__.py
│       ├── common.py     # 统一响应结构 (如 code, message, data)
│       ├── account.py
│       └── strategy.py
```

## 4. API 接口设计 (v1)
所有接口应返回统一的 JSON 结构，例如：`{"code": 0, "message": "success", "data": {...}}`

### 4.1 系统与监控 (System)
- `GET /api/v1/health`: 健康检查，无须鉴权 (返回服务是否运行、启动时间等)。

### 4.2 账户与交易 (Account)
- `GET /api/v1/account/balances`: 获取当前账户的资金/可用余额 (调用 `main_binance_client`)。
- `GET /api/v1/account/positions`: 获取当前所有持仓信息 (调用 `main_binance_client`)。

### 4.3 策略管理 (Strategy)
- `GET /api/v1/strategies`: 获取当前运行的所有策略列表及状态 (如绑定的交易对、K线周期)。
- *(未来扩展)* `POST /api/v1/strategies/{strategy_id}/stop`: 动态停止指定策略。

## 5. 核心改造点
1. **并发与入口重构**: 
   将 `run.py` 中的 `main()` 提取为可复用的 `BotManager`。在 FastAPI 的 `lifespan` 钩子中，使用 `threading.Thread` 启动 `BotManager.start()`。
2. **全局状态共享**:
   需要将 `main_binance_client` 和当前运行的 `handlers` (策略处理器) 注册到全局（或传递给 FastAPI `app.state`），以便在路由处理器中读取实时状态。
3. **安全配置**:
   在 `.env` 中增加 `API_ACCESS_KEY` 环境变量。所有 `/api/v1/account/*` 和 `/api/v1/strategies/*` 接口必须注入鉴权依赖。未提供有效 Key 将抛出 `401 Unauthorized` 异常并统一捕获返回。

## 6. 实施步骤 (TDD 流程)
- **步骤 1：API 基础设施与模型搭建**
  - 创建 `api` 目录及基础结构。
  - 编写 `api/schemas/common.py` 统一定义响应体。
  - 编写 `api/main.py` 和基础的健康检查路由 `/health`。
  - 编写测试用例 `tests/api/test_system.py`，使用 `fastapi.testclient.TestClient` 验证。
- **步骤 2：并发模型重构与 Lifespan 集成**
  - 重构 `run.py`，封装 `start_bot()` 逻辑。
  - 在 `api/main.py` 中实现 `lifespan`，确保后台线程能正常启动，并在主进程退出时安全关闭 EventLoop。
- **步骤 3：鉴权机制实现**
  - 编写 `api/dependencies.py`，实现 `verify_api_key` 依赖。
  - 编写相关鉴权拦截的单元测试。
- **步骤 4：业务路由实现 (Account & Strategy)**
  - 实现 `/account/balances` 和 `/account/positions` 路由，内部调用现有的 BinanceSwapClient 方法。
  - 实现 `/strategies` 路由，获取事件循环中注册的 handler 列表。
  - 为这些路由编写 Mock 测试。
- **步骤 5：统一启动入口整合**
  - 新增 `start_server.py` 或修改 `run.py`，使得默认启动方式为 `uvicorn api.main:app --host 0.0.0.0 --port 8000`。
