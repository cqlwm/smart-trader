# Smart Trader

面向币安 USDT-M 永续合约的自动化交易系统：以**信号网格策略**为核心，配合**预写记账 + 启动对账 + 持仓对账**的最终一致性账本，并提供浏览器控制台（只读视图 / 控制命令 / 行情 / 策略配置）。

策略决策与执行严格分层：信号只描述行情状态，入场由可组合闸门链判定，离场由可组合策略链判定，策略编排层只负责装配、仓位与订单管理。

## 功能特性

- **信号网格策略**：AlphaTrend 信号驱动，支持顺势/逆势加减仓、固定止盈、限价止盈、单笔止损、跟踪止损、达到仓位上限全平。
- **两种行情驱动**：每个策略配置的 `data_source` 决定使用 WebSocket 推送或 REST 定时拉取，统一入口可同时运行两种循环。
- **最终一致性账本**：sqlite + SQLAlchemy Core，下单前预写仓位/订单，`status` 为空表示结果未确认，启动时自动对账；每根完成 K 线再与交易所实际持仓对账。
- **风控与对账可观测**：外部手动平仓自动按 LIFO 收敛本地缺口（绝不下对冲单），来源不明仓位只告警不自动收编，支持人工仓位声明抑制告警。
- **Web 控制台（P0–P3）**：总览、持仓、订单、行情、策略配置；暂停/恢复/全平/撤入场单/全局 kill switch；K 线叠加 AlphaTrend 与订单标记。
- **工程化**：`uv` 依赖管理、Pyright strict、ruff、pytest 分层测试（单元 + 集成 + 崩溃恢复）。

## 架构一览

```
strategy / task                业务编排: 信号网格、入场闸门、离场策略、仓位生命周期
        │
        ├──▶ execution         执行编排: 追单器、记账下单 (JournaledTradingPort)、启动对账
        │          │
        │          └──▶ storage   账本: grid_positions / orders / commands / strategy_configs (sqlite)
        │          │
        │          ▼
        │      exchange/ports  端口 (ABC): MarketDataPort / TradingPort / AccountPort
        │          ▲
        └──────────┴── exchange/binance   适配器: ccxt 细节只存在于这里

web (React) ──HTTP──▶ api (FastAPI) ──▶ storage.reader (只读) / commands / configs

run.py（单进程） = api（控制台） + StrategyRuntime（按配置装配 WS / Timer 循环）
```

统一入口只有一个进程：`StrategyRuntime` 按每个策略配置的 `data_source` 装配事件循环，
没有启用配置或运行时装配失败时，控制台仍可启动（只读模式）。

强制依赖规则（详见 [.memory/01-system-architecture.md](.memory/01-system-architecture.md)）：

1. 业务层不得 import ccxt；只依赖 `smart_trader.exchange.ports` 的端口。
2. 适配器只做领域对象 ⇄ 交易所参数的翻译，不做重试、下单策略，不碰数据库。
3. 所有下单路径必须经过 `JournaledTradingPort`，预写与记账不可绕过。
4. 只读侧（控制台路由）不写账本、不调交易所；控制动作只能写 `commands` 表，由同进程的策略运行时（`CommandRunner`）认领执行。
5. storage 是纯数据层，SQLAlchemy 不越过 storage 边界。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `smart_trader/model.py` | 领域对象与枚举（Symbol / Kline / Order* / Position / 命令与信号枚举） |
| `smart_trader/exchange/` | 端口定义与币安适配器（公共行情实例 + 私有交易实例按能力分流） |
| `smart_trader/execution/` | 追单器、记账下单包装、启动订单对账 |
| `smart_trader/strategy/` | 信号、入场闸门、离场策略、网格策略、仓位生命周期与持仓对账 |
| `smart_trader/storage/` | 账本、配置、命令、人工仓位声明的 sqlite 仓储与只读查询 |
| `smart_trader/api/` | FastAPI 控制台后端（P0 只读 / P1 命令 / P2 行情 / P3 配置） |
| `smart_trader/task/` | 策略任务与命令轮询线程 |
| `smart_trader/strategy_runtime.py` | 策略运行时：按配置装配 WS / Timer 事件循环并管理启停 |
| `run.py` | 唯一进程入口：控制台 API + 策略运行时 |
| `template/` | 策略配置模板（种子脚本来源与参数示例） |
| `scripts/migrations/` | 编号、幂等的库结构演化脚本 |
| `web/` | React + Vite 控制台 |
| `tests/` | 单元测试、集成测试与测试替身 |
| `demos/` | 手工验证脚本（账本概览、交易所连通性、数据循环等） |
| `.memory/` | 架构、领域模型、编码设计文档（改动代码前必读） |

## 快速开始

### 1. 环境要求

- Python ≥ 3.11，依赖统一用 [uv](https://docs.astral.sh/uv/) 管理
- Node.js + npm（仅构建/开发前端时需要）

### 2. 安装依赖

```bash
uv sync
cd web && npm install && cd ..
```

### 3. 配置环境变量

在项目根目录创建 `.env`（已被 gitignore）：

```bash
# 必填：项目根目录绝对路径，账本写入 ${PROJECT_PATH}/.data/smart_trader.db
PROJECT_PATH=/path/to/smart-trader

# 必填：币安合约 API 凭据（建议只开合约交易权限，关闭提币，配置 IP 白名单）
BINANCE_API_KEY_MAIN=
BINANCE_API_SECRET_MAIN=
# True = 测试网/模拟盘；False = 实盘
BINANCE_IS_TEST_MAIN=True

# 可选
LOG_LEVEL=INFO              # 日志级别，默认 INFO
LOG_DIR=.data/logs          # 日志目录，默认 .data/logs
SMART_TRADER_API_HOST=127.0.0.1
SMART_TRADER_API_PORT=8000
SMART_TRADER_API_TOKEN=     # 设置后 API 要求 Authorization: Bearer <token>
```

### 4. 初始化数据库

首次启动 `run.py` 时，账本与控制面表会按 `schema.py` 自动创建；没有任何策略配置时，控制台以只读模式启动，可直接在「策略配置」页新建策略，因此**全新建库无需执行迁移**。

存量旧库按需执行结构迁移脚本（幂等，重复执行无害）：

```bash
DB=.data/smart_trader.db
uv run python scripts/migrations/001_add_grid_positions_closed_reason.py "$DB"
uv run python scripts/migrations/002_add_commands.py "$DB"
uv run python scripts/migrations/003_add_strategy_configs.py "$DB"
uv run python scripts/migrations/005_add_position_acks.py "$DB"
uv run python scripts/migrations/006_convert_per_order_amount.py "$DB"
```

如需从 `template/` 导入初始策略配置，再执行 `004_seed_template_configs.py`（不覆盖已编辑的配置）。

### 5. 启动服务

```bash
uv run python run.py       # 控制台 + 策略运行时, 默认 http://127.0.0.1:8000
```

- 同一进程内启动控制台 API 与策略运行时；启用的配置按各自的 `data_source` 进入 WS 或 Timer 循环，两种循环可并存。
- 没有任何启用的配置，或策略运行时装配失败（如凭据缺失）时，只记录日志，控制台仍可用。
- **代价**：API 与策略同进程，重启进程会同时中断交易；结构性配置变更与启停仍需重启，调参与新增策略可热加载。

### 6. 打开控制台

- 若已执行 `cd web && npm run build`，API 会一并托管 `web/dist`，直接访问 `http://127.0.0.1:8000`。
- 开发前端时使用 `cd web && npm run dev`，Vite 将 `/api` 代理到 `127.0.0.1:8000`。

## 策略配置

策略配置持久化在 sqlite 的 `strategy_configs` 表中，可在控制台「策略配置」页新建/修改，也可用种子脚本从 `template/` 初始化。

- **生效时机**：运行时每 2 秒轮询配置表。**新增策略与调参自动热更新**；交易对 / 周期 / 数据源 / 持仓方向 / 入场方向**创建后不可修改**；**停用**仍需**重启 `run.py`**（不替换运行中的策略实例，避免孤立在途订单与开放仓位）。启用一个当前未运行的配置会走热新增。
- **热新增**：新增启用配置会挂到对应 WS / Timer 循环；WS 新 symbol 或周期会触发一次重连并按新订阅列表重放。
- **数据源选择**：每个配置的 `data_source` 决定进入 WebSocket 还是 REST 定时拉取循环；两种循环可在同一进程内并存，创建后不可切换。
- **`strategy_key` 与身份字段不可变**，且不提供删除，只允许 `enabled=false`，避免与历史账本失联；需要换交易对 / 周期 / 方向时请新建配置。
- 该 `strategy_key` 存在活跃仓位时，禁止停用配置。

主要参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `symbol` / `timeframe` | `DOGE/USDT` / `1m` | 交易对与 K 线周期 |
| `data_source` | `timer` | 行情驱动方式：`ws`（WebSocket 推送）或 `timer`（REST 定时拉取） |
| `signal_type` / `signal_kind` | `alpha_trend` / `directional` | 信号实现：`alpha_trend`（原始方向）/ `alpha_trend_inverse`（反向解读）/ `none`；`directional` 有方向倾向，`trigger` 任何非中性信号都触发 |
| `atr_multiple` / `period` | `1.0` / `8` | AlphaTrend 参数 |
| `position_side` / `master_side` | `long` / `buy` | 持仓方向与网格主攻方向 |
| `per_order_amount` / `per_order_unit` | `0.02` / `base` | 每层规模；`base` 为币数量，`quote` 为 USDT 名义价值（开仓时按信号收盘价折算并向下取整到 `step_size`） |
| `max_order` | `10000` | 最大逻辑仓位数（上限闸门与上限止损的唯一来源） |
| `grid_spacing_rate` | `0.01` | 网格间距。正数：仅亏损方向走出间距才加仓；负数：距基准不超过 \|值\| 即加仓（盈利方向也放行） |
| `trend_spacing_rate` | 空 | 非空时启用顺势阶梯加仓，与 `grid_spacing_rate` 语义互斥 |
| `enable_signal_take_profit` | `true` | 反向信号命中且该层浮盈 → 离场 |
| `enable_signal_stop_loss` | `false` | 反向信号命中且全仓浮亏层 ≥ 2 → 全部浮亏层强制离场 |
| `fixed_rate_take_profit` / `fixed_take_profit_rate` | `false` / `0.006` | 固定比例止盈 |
| `take_profit_use_limit_order` | `false` | 是否在入场成交后直接挂限价止盈单 |
| `place_order_behavior` | `chaser_open` | `normal` / `chaser`（追单并等成交）/ `chaser_open`（追单只挂单） |
| `enable_order_stop_loss` / `order_stop_loss_rate` | `false` / `0.05` | 单层价格维度止损，按仓位均价实时计算 |
| `enable_trailing_stop` / `trailing_stop_rate` / `trailing_stop_activation_profit_rate` | `false` / `0.02` / `0.01` | 跟踪止损（激活后按高点回撤 ratchet） |
| `enable_max_order_stop_loss` / `paused_after_stop_loss` | `false` / `true` | 达到仓位上限时全平，并可暂停策略 |

`alpha_trend_inverse` 在**信号层**反转方向：配置为 `position_side=long` 时，AlphaTrend 的看跌突破触发做多，看涨突破触发信号离场；因入场闸门与离场策略共享同一信号实例，两侧同时反向。单个配置仍只持一个方向，多空双向反手需 long / short 两个配置（交易所为双向持仓模式）。

`per_order_unit=quote` 时每层规模按名义价值设置（如 `100` USDT）：开仓时数量 = 金额 / 信号收盘价，再按交易所 `stepSize` 向下取整；折算后低于最小数量或最小名义价值时拒绝开仓并记录日志。该金额是订单名义价值（价格 × 数量），不是保证金；实际成交名义价值会因取整与追单价格漂移略有偏差。

## 控制台与 API

页面：总览、持仓、订单、行情、策略配置。

| 方法 | 端点 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务时间、账本路径、结构校验状态 |
| GET | `/api/overview` | 策略/仓位/订单统计、已实现盈亏、未确认订单与对账告警 |
| GET | `/api/strategies` | 按 `strategy_key` 聚合的策略摘要（含运行态与对账快照） |
| GET | `/api/positions` | 逻辑仓位派生视图 |
| GET | `/api/positions/{id}` | 仓位详情与全部订单 |
| GET | `/api/orders` | 订单分页（`status=unconfirmed` 映射 `status IS NULL`） |
| GET | `/api/market/chart` | 公共 K 线 + AlphaTrend 指标点 |
| GET / POST / PUT | `/api/configs` 系列 | 策略配置查询、新建、乐观锁更新 |
| POST | `/api/strategies/{key}/commands` | `pause` / `resume` / `close_all` / `cancel_entries` |
| POST | `/api/commands/kill-switch` | 全局 kill switch：先全平再暂停 |
| GET | `/api/commands` | 命令审计 |
| GET / POST | `/api/position-acks` 系列 | 人工仓位声明与撤销（仅影响告警，不改变账本与交易） |

控制台路由对账本只发 SELECT，写操作仅限 `commands` / `strategy_configs` / `position_acks`，绝不写仓位与订单、不调交易所；同进程内的策略运行时是账本唯一写者（进程合并，职责不合并）。

## 账本与一致性

- 逻辑仓位只存策略参数与运行时状态，**价格、数量、相位全部由订单流水派生**（数量 = 基准 + 入场成交 − 出场成交）。
- 下单前先写仓位行与订单行（首单同事务），订单行 `status` 为空表示结果未确认；进程崩溃后由启动对账用交易所事实回写或删除。
- 出场是有生命周期的：挂单 ≠ 了结，只有出场全部成交且可平量归零才归档。
- 每根完成 K 线做持仓对账：本地多于交易所 → 按 LIFO 内部归档并修正可平量；交易所多于本地 → 仅告警，等待人工确认。

完整不变量与状态机见 [.memory/01-system-architecture.md](.memory/01-system-architecture.md) 第十、十一节与 [.memory/02-domain-model.md](.memory/02-domain-model.md)。

## 开发

```bash
uv run pytest              # 单元测试（默认排除 integration）
uv run pytest -m integration  # 集成测试（真实 sqlite + 状态化假交易所 + 子进程崩溃恢复）
uv run pyright             # strict 类型检查，必须 0 errors / 0 warnings
uv run ruff check .        # Lint
```

开发规范（TDD 红-绿-重构、7 类必测场景、类型与日志约定、迁移约定）见 [AGENTS.md](AGENTS.md)；改动代码前按顺序阅读：

1. [.memory/01-system-architecture.md](.memory/01-system-architecture.md) —— 分层、模块职责、设计模式
2. [.memory/02-domain-model.md](.memory/02-domain-model.md) —— 领域模型、表结构、字段含义
3. [.memory/03-coding-design.md](.memory/03-coding-design.md) —— 可测试性与扩展设计

库结构演化：每次变更在 `scripts/migrations/` 新增下一个编号的幂等脚本，手动对每个存量库执行一次；业务代码不内嵌迁移，`schema.py` 是当前结构的唯一声明。

调试辅助：

```bash
uv run python demos/positions_overview_demo.py   # 按 strategy_key 汇总有效仓位与已实现盈亏
uv run python demos/binance_exchange_demo.py     # 交易所适配器连通性
```

## Docker 部署

```bash
# 构建镜像并启动唯一入口（控制台 + 策略运行时）
./start_using_docker.sh

# 手动运行等价命令（脚本已包含以下参数）
docker run --name smart-trader --restart=always \
  -v ./.env:/usr/local/app/.env \
  -v ./.data:/usr/local/app/.data \
  -e PROJECT_PATH=/usr/local/app \
  -e SMART_TRADER_API_HOST=0.0.0.0 \
  -p 127.0.0.1:8000:8000 \
  -d smart-trader:0.0.2
```

启动后访问 `http://127.0.0.1:8000`。`.env` 与 `.data` 需挂载进容器以持久化账本与日志；容器内必须把 `PROJECT_PATH` 覆盖为 `/usr/local/app`，否则账本会写进容器内部而不是挂载卷。

## 策略交易实践

1. 确认宏观方向，使用动态网格捕捉趋势。
2. 确认趋势后开启长线交易，并用动态网格确认趋势反转。

## 风险提示

本项目仅供研究与学习使用。合约交易具有高杠杆与高波动风险，任何策略参数、回测或实盘表现都不构成收益承诺。请先在测试网/模拟盘验证，并自行承担交易风险。
