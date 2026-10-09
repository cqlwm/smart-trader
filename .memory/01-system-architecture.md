# 系统架构

## 一、分层与依赖方向

```
strategy / task / template              (业务编排: 网格、信号、止损)
        │
        ├──▶ execution (smart_trader/execution/)   执行编排: 追单、记账下单、对账
        │          │  │
        │          │  └──▶ storage (smart_trader/storage/)   订单日志: id/仓位/流水表 (sqlite, SQLAlchemy Core)
        │          │
        │          ▼
        │      ports (smart_trader/client/ports.py)   零实现、零第三方依赖
        │          ▲
        │          │  适配器实现端口
        └──────────┴── adapters (smart_trader/client/binance_client.py)
                           ccxt / REST 细节只存在于这里
```

依赖规则（强制）：

1. **业务层不得 import ccxt**，不得出现 `exchange`、`setup_*` 等交易所 SDK 概念；需要行情/交易/账户能力时依赖端口。
2. **适配器只做翻译**：领域对象 ⇄ 交易所参数/响应。不做重试编排、不做下单策略、不碰数据库。
3. **领域对象是唯一契约**：跨层传递的订单/持仓必须是 `OrderRequest` / `OrderResult` / `Position`，禁止使用 `dict[str, Any]` 承载订单语义。
4. 交易所异常在适配器内统一翻译为 `SmartTraderError` + `ErrorCode`，业务层不感知 ccxt 异常类型。
5. **storage 是纯数据层**：只做 sqlite 存取与事务，只依赖 `model` 与 SQLAlchemy（SQLAlchemy 仅限 storage 内部，对外只暴露 Pydantic 记录与 `OrderJournal`）；不 import ccxt、不依赖 execution / strategy。storage 之外的任何层不得 import sqlalchemy。
6. **所有下单路径必须经过 `JournaledTradingPort`**（策略常规单、追单的每次尝试）：id 预留与记账不可绕过，否则最终一致性失效（见第十一节）。

## 二、端口定义

`smart_trader/client/ports.py`，全部为 `Protocol`，按调用方需求拆分（接口隔离）：

| 端口 | 职责 | 方法 |
| --- | --- | --- |
| `MarketDataPort` | 只读行情与交易规则 | `symbol_info` / `latest_price` / `fetch_ohlcv` |
| `TradingPort` | 下单与订单查询 | `place` / `query` / `cancel` |
| `AccountPort` | 账户与持仓 | `balance` / `positions` |

调用方按需注入，例如 `SignalGridStrategy` 只接受 `trading` + `market`，不持有账户能力。

## 三、执行策略（追单）的位置

`LimitOrderChaser`（`smart_trader/execution/limit_order_chaser.py`）是**执行策略**，不是适配器：

- 只依赖 `TradingPort` + `MarketDataPort`，不 import ccxt；
- 通过 GTX（post only）限价单逐档递进价格争取 maker 成交；
- 是否等待成交由调用方决定（`wait_for_fill`）。

调用关系：`SignalGridStrategy` 依据 `PlaceOrderBehavior` 决定用 `LimitOrderChaser` 还是直接 `TradingPort.place`；追单耗尽仍未挂上时，降级为一次常规限价单。

## 四、状态机（订单）

```
         place()                   交易所回报
  [无] ──────────▶ OPEN ────────────────────────▶ CLOSED   (完全成交)
                    │                                    
                    ├──────────────────────────▶ CANCELED (撤单 / 超时撤单)
                    ├──────────────────────────▶ REJECTED (确定性拒绝)
                    └──────────────────────────▶ EXPIRED  (过期, 含"查不到订单")
```

- 终态：`CLOSED` / `CANCELED` / `REJECTED` / `EXPIRED`（`OrderStatus.is_finished`）。
- `query`/`cancel` 遇到"订单不存在"返回 `None`，由调用方决定语义（策略层视为 `EXPIRED`）。
- 部分成交必须通过 `OrderResult.filled_qty` 传递，禁止用布尔值表示"成交完成"。

## 五、异常策略

- 适配器翻译：post only 被拒 → `ORDER_WOULD_TAKE_LIQUIDITY`；确定性拒绝 → `ORDER_REJECTED`；余额不足 → `INSUFFICIENT_FUNDS`；限频/网络 → `EXCHANGE_RATE_LIMITED` / `EXCHANGE_UNAVAILABLE`；其它 → `EXCHANGE_ERROR`。
- 可重试 vs 不可重试由 `ErrorCode` 区分，重试逻辑在调用方（如追单器）。
- 业务层捕获 `SmartTraderError` 记录日志，不让单次下单失败终止整个数据循环。

## 六、模块地图

| 模块 | 职责 |
| --- | --- |
| `smart_trader/model.py` | 领域对象与枚举（Symbol / SymbolInfo / Kline / Order* / Position） |
| `smart_trader/client/ports.py` | 端口（Protocol） |
| `smart_trader/client/binance_client.py` | Binance 适配器 + ccxt ⇄ 领域对象翻译 |
| `smart_trader/storage/records.py` | 存储记录模型（Pydantic）：`GridPositionRow` / `OrderRecord`，存储层对外的数据契约 |
| `smart_trader/storage/schema.py` | 表结构元数据（SQLAlchemy Core）：schema 唯一来源（列/约束/索引），建库走 `metadata.create_all` |
| `smart_trader/storage/order_journal.py` | 订单日志仓储（SQLAlchemy Core 语句 + 事务）：`grid_positions`（逻辑仓位）+ `orders`（订单，`status` 为空承担 WAL） |
| `smart_trader/execution/limit_order_chaser.py` | 追单执行策略 |
| `smart_trader/execution/journaled_trading.py` | 记账下单包装（TradingPort 装饰器）：预写仓位/订单 → 下单 → 回写结果 |
| `smart_trader/execution/order_reconciler.py` | 启动对账：扫 `status IS NULL` 的订单 → 查交易所 → 回写 |
| `smart_trader/strategy/` | 策略基类与网格策略；信号网格拆分为 `grid_position`（仓位对象 + `load_positions` 批量还原）/ `signal_grid_config`（配置 + `CloseOutcome`）/ `grid_order_gateway`（记账下单入口）/ `position_lifecycle`（订单结算与出场单跟踪）/ `signal_grid_strategy`（编排 + 仓位登记表 + 公共名再导出） |
| `smart_trader/data_event_loop.py` / `task/` | WebSocket 事件循环与任务分发 |

## 七、演进状态

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| Step 0 | 架构文档落地（本文） | 已完成 |
| Step 1 | 领域订单对象 + 端口签名 + 适配器翻译 + 追单器解耦 + 删除 `ExSpotClient`/`place_order_v2`/`exchange_name` | 已完成 |
| Step 2 | `DataEventLoop` 任务异常隔离 + K 线解析下沉；平仓按实际成交量记账 | 已完成 |
| Step 3 | 去掉 `_v2` 命名（`grids_strategy_v2` → `signal_grid_strategy`）；`OrderRecorder.record` 死逻辑修正 + 原子落盘 | 已完成 |
| Step 4 | WebSocket 断线重连（`on_close` 不再终止循环）+ 可注入会话工厂 + 优雅退出 | 已完成 |
| Step 5 | 见下"待办清单" | 待办 |
| Step 6 | `order_file_path` → `strategy_key`；sqlite 订单日志（id 表 WAL + order 表）；`JournaledTradingPort` 记账下单 + 启动对账（见第十一节） | 已完成 |
| Step 7 | 语义正名：`Order` → `GridPosition`（逻辑仓位）、`OrderManager` → `GridPositionManager`；行为零变化 | 已完成 |
| Step 8 | 数据模型拆分：`orders` 表拆为 `grid_positions`（逻辑仓位）+ `orders`（纯订单流水）；storage 层命名对齐（`OrderRow` → 分家） | 已完成 |
| Step 9 | 存储层 SQLAlchemy Core 化：拆分 `records` / `schema` / `order_journal`；DDL 单一来源；泛型 upsert；WAL + busy_timeout；DDL 等价性测试（设计见 `.memory/03-coding-design.md`） | 已完成 |
| Step 10 | 逻辑仓位重构：`grid_positions` 只存参数与运行时状态，价格/数量/相位由 `orders` 派生；删 `order_ids`（WAL 改由 `orders.status` 为空承担）；入/出场按 `position_side` 归类；`current_stop_price` → `trailing_stop_price` | 进行中 |
| Step 11 | 删除 `GridPositionManager`：仓位登记表内联进策略（`positions` / `add_position` / `remove_positions`），批量还原下沉为 `grid_position.load_positions`；`load_positions(force)` 的 `force` 参数随之删除（唯一调用点在 `__init__`）；行为零变化 | 已完成 |
| Step 12 | 回调互斥重构：`on_kline_lock` + `on_kline_finished_lock` 合并为 `_callback_lock`；`_call_on_kline_finished` 阻塞等待（交易决策永不被跳过），`_call_on_kline` 非阻塞（盘中更新可跳过 + warning 日志）；`SignalGridStrategy._positions_lock` 随之删除（回调已串行化） | 已完成 |

### 待办清单（Step 5）

按优先级：

1. **网格策略关键分支缺测试**（当前仅 ~50% 有效覆盖）：
   `position_reverse`（反向持仓会翻转 side 与 position_side）、
   `enable_max_order_stop_loss` + `paused_after_stop_loss`（达到上限止损后暂停策略）、
   `per_order_qty == 0`、`profit_level` / `profit_and_loss_ratio` 的分档边界、
   `grid_spacing_rate` 的加仓间隔判定、`close_position` 手动全平。
2. **信号层缺单元测试**：`Signal.is_entry` / `is_exit` 的买卖方向语义，
   以及 `AlphaTrendGridsSignal`（entry 与 exit 返回同一个条件，需明确这是不是有意设计）。
3. **`AlphaTrendGridsSignal` 死代码**：`_close`、`_alpha_trend_cross_buy/sell`、
   `_alpha_trend`、`_long_trend`、`_short_trend` 定义后从未使用。
4. **`.memory/03-coding-design.md` 缺失**（AGENTS.md 第三节要求）。
5. **日志与配置模块无测试**（`log.init_logging` 的级别解析、`config` 的必填校验）。

### 已修复缺陷（记录）

- `OrderRecorder.record` 的 `refresh_orders or len(latest_orders) != len(self.orders)`
  在赋值后比较，恒为 `False`，属无效逻辑；已移除，并要求有平仓时强制落盘。
- 落盘由 `open(..., "w")` 直接写入改为"临时文件 + `os.replace`"原子替换。
- **入场单部分成交时平仓触发会丢敞口**：原逻辑撤销剩余挂单后连同已成交部分一起丢弃。
  修复：查询回写成交量 → 撤剩余挂单 → 复查终态（撤单前可能又有成交）→
  **零成交才丢弃，已成交部分按实际成交量纳入平仓**。
- **出场单部分成交后重挂未扣除已成交量**：会挂出超量平仓单（如已平 30 仍重挂 100）。
  修复：重挂量 = `实际成交量 − 出场单已成交`；剩余 ≤ 0 视为平仓完成直接归档。
- 撤单与复查统一走 `_cancel_and_refresh`：先撤单再查询终态，避免"撤销前又成交一部分"的竞态。
- **出场单未成交导致敞口脱管**：原逻辑在挂出出场单后即把仓位归档（`active=0`），
  限价单未成交时仓位不再被跟踪，重启后 `load_positions` 也加载不到。
  修复：出场改为显式生命周期（`HOLDING → EXIT_PENDING → ARCHIVED`），
  只有出场单确认成交且可平量耗尽才归档；跟踪每根 K 线无条件执行
  （止损暂停后仍执行）；未成交保留原挂单，强制出场才撤单重挂（见第十节不变量 4）。
- **多仓位共用一张聚合出场单导致按仓位归因错误**：部分成交时每个仓位都用同一聚合成交量扣减。
  修复：改为每仓位一张独立出场单，成交可独立归因。

## 九、事件循环与任务

- `Task` 必须提供 `name`（用于日志定位），并统一经 `Task.safe_run` 调度：
  任务异常在此被捕获并记录，不会中断事件循环，也不会被线程池静默吞掉。
- 消息解析是纯函数（如 `parse_kline_message`），无法解析时返回 `None` 并记 debug 日志，
  不允许向上抛异常。
- `StrategyTask` 按 `symbol + timeframe` 过滤消息，非本任务的 K 线直接忽略。

## 十、持仓记账不变量

逻辑仓位（`GridPosition`）只存策略参数与运行时状态；**价格、数量、相位全部由 `orders` 派生**
（定义见 `.memory/02-domain-model.md` 第七节）。

1. **数量 = 基准 + 入场成交 − 出场成交**：`quantity = opening_quantity + entry_filled − exit_filled`。
   平仓可用量只能取派生后的 `quantity`；任何"按委托量平仓"的写法都会在部分成交场景下超卖。
2. **入场/出场按 `position_side` 归类**：`long` → buy=入场、sell=出场；`short` 反之。
   `main_order_side` 只表示网格主攻方向，**不参与**归类。
3. **`OrderStatus.CLOSED` 等价于完全成交**：ccxt 把部分成交映射为 `open`，
   因此适配器在 `closed` 且无 `filled` 字段时按委托量补齐成交量。
4. **未成交的订单不产生平仓单**：成交量 > 0 才会进入平仓队列。
5. **出场是有生命周期的（挂单 ≠ 了结）**：
   - "未成交出场单" = 存在 `status ∈ {OPEN, 空}` 的出场行；其挂单价与数量由此行派生；
   - 仓位只有在**出场全部成交且 `quantity` 归零**时才归档（`active=0`）；
   - 每仓位可有**多条**入场/出场订单，成交按订单行独立归因；
   - 出场单跟踪每根完成 K 线**无条件**执行：不被开仓短路、止损暂停（`is_running=False`）后仍执行。
   - 未成交且价格回落 → **保留原挂单**；出场单作废（canceled/rejected/expired / query 不到）
     → 结算已成交后退回持仓，等待重挂；查询异常 → 下一根重试，绝不重复下单。
   - **强制出场**（单笔止损 / 达到上限全平 / 手动 `close_position`）可覆盖休息中的挂单：
     先撤单、结算已成交部分，再按剩余重挂。
6. **相位（派生，不落库）**：`active=0 → closed`；`quantity<=0 → opening`（虚拟空仓位）；
   `quantity>0 且 has_pending_exit → closing`；否则 `holding`。出场单被撤后退回 `holding`。
7. **孤儿回收**（每根完成 K 线执行）：
   - 孤儿条件 = `active=1` 且 `quantity<=0` 且无任何存活订单（无 `OPEN`、无 `status` 为空的行）；
   - **超时回收**：必须同时满足孤儿条件**且**仓位存在时长超过 `max(10 分钟, 策略 K 线周期时长)`
     才回收 —— opening 空仓位先保留作网格价格参考，超时后再清（回收即置 `active=0`）。

## 八、测试策略

- 内存实现优先：`tests/fakes.py` 提供 `FakeCcxtExchange` / `FakeTradingPort` / `FakeMarketDataPort`，默认测试不触网（集成测试标记 `@pytest.mark.integration`）。
- 端口契约测试通过假端口验证业务行为，适配器测试通过假 ccxt 验证翻译规则。
- sqlite 日志测试统一使用 `:memory:` 数据库（StaticPool 共享单连接），不落盘、不共享状态。
- 库结构由 `tests/test_storage_schema.py` 以冻结的旧版 DDL 快照做等价性校验：metadata 建出的库与历史契约（列/约束/默认值/索引）不一致即失败。

## 十一、订单最终一致性（预写 + 对账）

背景：`place_order` 是"多步、可中断"的动作，任何一步崩溃都可能留下"交易所已有单、本地不知道"的幽灵订单。用"预写 + 启动对账"兜住。**原 `order_ids`（WAL）表已删除**，其职责由 `orders` 行承担（`status` 为空 = 结果未确认）。

### 流程（见 storage/order_journal.py）

1. **预写**：向交易所发送前先把仓位行（首单时**同事务**创建）与订单行写入库，订单行 `status` 置空；
2. **发送**：用本地生成的 `order_id` 调交易所（追单每次递进复用同一 id，除非已确认订单存在）；
3. **回写**：
   - 结果明确（挂上 / 拒绝 / 取消 / 过期，订单确曾存在）→ 回写该行 `status/filled_qty/avg_price`；
   - 结果未知（网络超时、响应丢失、进程崩溃）→ **保持 `status` 为空**，交给对账；
   - GTX 被拒（-5022）等"交易所从未存在的订单" → **删除预写行**，且该 `order_id` **仍可复用**；
   - **id 复用规则**：只要确认订单从未在交易所存在，就复用同一个 `order_id` 重试（同一 id 不会堆出多行）；
     一旦订单存在（被接受 / 有成交），该 id **作废**，后续下单必须换新 id。

### 状态语义（不变量）

- **`status` 为空 ⇔ 该订单结果未确认**（不是"没发过请求"）；`status IS NULL` 就是待对账清单。
- **仓位与首单必须同事务**：避免"有仓位无订单"或"有订单无仓位"的孤儿。
- **策略参数先于下单写入**：策略参数存于 `grid_positions`，交易所只提供客观事实；
  对账只需 `symbol + grid_position_id`（均在订单行上），不再需要意图快照。

### 对账（storage 之外，execution/order_reconciler.py）

- 触发时机：策略启动加载仓位前（`force` 加载）；
- 规则：对每条 `status IS NULL`：
  - 距创建时间过近（< 最小年龄）→ 跳过，留给下一次；
  - query 到有效订单（open / closed / 有任何成交）→ 用交易所事实回写该行；
  - query 不到 → 删除该行（交易所从未存在）。
- 对账查询异常单条容忍，不中断整个对账。

### 归谁调

- 记账包装 `JournaledTradingPort` 实现 `TradingPort`：`place` 内完成 预写 / 回写；`query` / `cancel` 透传。策略与追单器只依赖端口，不感知日志存在。
- 策略加载：`strategy_key` → `OrderJournal.load_active_positions`（含关联订单聚合）。
