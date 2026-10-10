# 编码设计

本文件沉淀跨模块的编码设计决策与约束（AGENTS.md 第三节要求）。
修改代码前先查阅本文档；设计变更时先更新本文档，再动代码。

## 一、总则

1. **记录模型用 Pydantic**：DB 行 → Record → Domain → Dict 的既有转换模式（见 AGENTS.md 第五节），
   禁止用 `dict[str, Any]` 承载业务语义；`extra="forbid"` 保持开启，字段拼写错误立即报错。
2. **易于测试**：外部依赖必须有内存实现（`tests/fakes.py`）；测试不触网、不落盘、相互独立。
3. **可扩展**：一切"复制粘贴的第二份真相"都是缺陷——同一信息只允许有一个权威来源，
   其余位置从权威来源派生或引用。
4. **可读性**：模块按职责拆分，单文件不允许混装"数据契约 + 表结构 + 查询逻辑"。

## 二、存储层设计（storage）

### 1. 模块职责

| 文件 | 职责 | 依赖 |
| --- | --- | --- |
| `storage/records.py` | 记录模型（Pydantic）：`GridPositionRow` / `OrderRecord` | `model` |
| `storage/schema.py` | 表结构元数据（SQLAlchemy Core `Table`）：列/约束/索引 | `sqlalchemy` |
| `storage/order_journal.py` | `OrderJournal` 仓储：语句构造 + 事务 + 连接管理 | `records` / `schema` |

**SQLAlchemy 只允许出现在 storage 内部**（`schema.py` / `order_journal.py`），
对 execution / strategy / 测试暴露的永远是 Pydantic 记录与 `OrderJournal`。

### 2. schema 单一来源

- 列定义只在 `schema.py` 出现一次；建库走 `metadata.create_all()`（替代硬编码 DDL 字符串）。
- 列类型刻意与历史 DDL 对齐（`TEXT` / `INTEGER` / `REAL`），保证新旧库完全兼容。
- 有意调整库结构时必须：改 `schema.py` → 更新 `tests/test_storage_schema.py` 的冻结快照 →
  评估旧库迁移；任何**无意**漂移都会被等价性测试抓住。
- 全部语句一律使用 Core 构造，不保留裸 SQL 分支（旧库的裸 SQL 迁移逻辑已随
  `order_ids` 表一并删除）。

### 3. 语句构造（为什么是 Core 而不是 declarative ORM / SQLModel）

- 三张表**零外键、零关系、零对象图**，declarative ORM 的 relationship / unit-of-work / identity map
  没有用武之地；引入它只会带来第二套模型体系（双映射）或挤掉 Pydantic 的记录语义。
- SQLModel 的"单类双用"看似省事，但 pyright strict 兼容性与 0.0.x 稳定性不满足本项目
  （0 errors / 0 warnings）的门禁要求。
- Core 恰好覆盖痛点：schema 单源、参数化 UPSERT、映射自动化。
- 若未来真需要 declarative ORM，现有 `Table` 可被 imperative mapping 直接复用，不是单行道。

### 4. 泛型 upsert

- `_upsert(conn, table, primary_key, record)`：列值来自 `record.model_dump(mode="json")`；
- 冲突时更新**除主键与 `created_at` 外**的全部列——`created_at` 首写不变，与旧实现语义一致；
- `on_conflict_do_update` 的 `set_` 是 dict，杜绝"位置参数错一位 = 静默串数据"。

### 5. 连接与事务模型

- 每个方法用 `engine.begin()` / `engine.connect()` 独占一次连接与事务，**替代**旧的
  共享连接 + `threading.RLock`；方法内部不再需要锁。
- 文件库：连接时设置 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000`，
  并发写由 SQLite 自行串行化（多策略共享一个库的主要并发路径是"写-写"）。
- 内存库（测试）：`StaticPool` + `check_same_thread=False`，单连接共享。
- 原子性不变量不变：`JournaledTradingPort.place` 预写的"仓位行 + 订单行"必须同一事务；
  订单行 `status` 为空即"结果未确认"，是 WAL 标记也是对账线索
  （见 `01-system-architecture.md` 第十一节）。

### 6. 映射（Row → Record）

- 常规表：`GridPositionRow.model_validate(dict(row))`——列名与字段名 1:1，
  枚举 / 布尔由 Pydantic 宽松模式从 DB 原始值恢复（`"buy"` → `OrderSide.BUY`，`0/1` → `bool`）。
- 映射代码不引入独立适配层；若某列未来需要特殊转换，在该列的读取处就近处理并补测试。

### 7. 已知权衡（记录在案）

- `:memory:` + StaticPool 单连接在**多线程并发**下共享同一 sqlite 连接；
  当前测试为单线程，行为无影响；生产走文件库（多连接 + WAL）。若未来出现内存库多线程测试，
  需要先升级为"每线程独立库"或改用文件库。
- `rowid` 排序（`load_positions` / `load_archived_positions` / `load_order_records` 的稳定 tie-break）
  通过 `text("rowid")` 保留，不进入 metadata（SQLite 隐式列，无需定义）。

## 三、信号与规则层设计（strategy）

### 1. 四层职责与单向依赖

| 层 | 文件 | 职责 | 形态 |
| --- | --- | --- | --- |
| `Signal` | `strategy/signal.py` | 只回答"行情现在是什么状态" | ABC，1 个抽象方法 |
| `EntryGate` | `strategy/entry_gate.py` | 可组合的入场闸门链 | ABC + `EntryGateChain` |
| `ExitPolicy` | `strategy/exit_policy.py` | 可组合的离场策略链 | ABC + `ExitPolicyChain` |
| `SignalGridStrategy` | `strategy/signal_grid_strategy.py` | 装配 + 仓位与订单管理 | 编排者 |

依赖方向单向：`strategy` → `entry_gate` / `exit_policy` → `model`。
**gate / policy 不得 import 任何 strategy 模块**（`grid_position` 是领域对象，位于 `strategy/` 下但
不依赖策略逻辑，允许被引用）。

### 2. `Signal` 为什么没有 `side`

旧 `Signal.__init__(side: OrderSide)` 把"信号方向"（行情事实）与"持仓方向"（策略决策）
压在同一个 `OrderSide` 上，导致三处语义互相缠绕：

- `side` 与 `position_reverse` 各自独立地翻转方向，二者关系无定义；
- `reverse` 只能反转 `run()` 的输出，不反转内部 `current_signal`，状态与输出长期不一致；
- `AlphaTrendGridsSignal` 被迫同时覆写 `is_entry` 与 `is_exit`（两者返回同一表达式）
  才能绕过 `side` 的方向映射 —— 即子类推翻了基类语义。

设计结论：**方向是行情事实，由输出表达**。`Signal.evaluate` 返回 `MarketSignal`，
方向消费方（Gate / Policy）按 `SignalKind` + 策略的 `entry_side` / `exit_side` 推导。
策略内不出现任何方向比较。

### 3. 为什么 `signal: Signal | None` 而不是 `NoSignal` 空对象

`NoSignal.evaluate` 恒返回 `NEUTRAL`，会让 `SignalTriggerGate` **全部拒绝**入场；
而"未配置信号"的既有语义是**放行**。两者相反，空对象不能替代可空引用。

可空性收敛在**唯一三处**：`_build_entry_gates` / `_build_exit_policies` / `_current_market_signal`。
Gate 内统一 `if ctx.market_signal is None → 放行`。

### 4. 为什么用 `SignalKind` 枚举而不是 Gate 的 bool flag

`TRIGGER`（无方向倾向）/ `DIRECTIONAL`（有方向倾向）是**信号种类**（领域概念），
应进 `model.py` 与 config。bool flag（如 `require_direction: bool`）会把
"闸门实现细节"泄漏成配置契约，且无法一词表达 `AlphaTrendGridsSignal` 的完整语义。
装配时按 `signal_kind` 二选一，而不是让一个 Gate 承担两种模式。

### 5. `*Context` 是纯数据，不放 `Kline` / `DataFrame`

`EntryContext` 只放 `{positions, close, market_signal}`。理由：

- `Kline` 是手写类、`DataFrame` 需 `arbitrary_types_allowed`，放进 Pydantic 会引入序列化陷阱
  （`model_dump()` 失败）；
- 盘中路径需要 `kline.high/low`，但那是**订单跟踪**职责，留在策略里。

`close: float | None` 用 `None` 表达"尚无行情"；需要确定值时调 `require_close()`
抛 `SmartTraderError`，**不用 `assert`**（`python -O` 下会被剥离）。

### 6. Gate/Policy 是普通类，Context/Decision 是 Pydantic

- Gate / Policy 是**行为对象**（同 `LimitOrderChaser` / `GridOrderGateway` / `PositionLifecycle`），
  持有构造期注入的不可变参数；
- Context / Decision 是**数据契约**（同 `records.py` / `GridPosition`），统一 `extra="forbid"`。

### 7. 短路与合并规则

- `EntryGateChain.allows`：**短路 AND**，空链恒放行。
- `ExitPolicyChain.decide`：`should_exit = any`、`force = any`。
- **决策合并顺序无关** —— 这是 `MaxOrderStopLossGate` 能从嵌套的"间距达标"分支里
  平铺进闸门链的根本依据（纯谓词 AND 可交换，布尔结果不变，仅评估次数变化）。

### 8. `force` 语义

`ExitDecision.force = True` 表示"**可覆盖休息中的出场挂单**（撤单结算后重挂）"。

不变量：**已有未成交出场单时，只有 `force=True` 的决策可覆盖；普通止盈保留原挂单。**

只有两类策略产出 `force=True`：

| 策略 | 语义 |
| --- | --- |
| `ForceExitPolicy` | 仓位外部强制（达到上限止损 / 手动 `close_position`） |
| `StopLossPolicy` | 仓位自身止损 |

止盈类策略（`FixedTakeProfitPolicy` / `SignalExitPolicy`）恒为 `force=False`。

`RestingTakeProfitPolicy` **不进 `ExitPolicyChain`**：它不判断是否离场，只提供挂单价，
塞进 `ExitDecision` 就是死字段。

### 9. 信号求值时机与 v2 指标口径

策略按 K 线 `datetime` memo（`_current_market_signal`），保证每根 K 线最多一次 `evaluate`。
`AlphaTrendSignal` 自身的 `datetime` 缓存保留 —— 它的独立契约要求自包含
（同一根 K 线重复调用必须返回相同结果，幂等）。

**`AlphaTrendSignal` 采用 `alpha_trend_indicator_v2.py` 的趋势累积计数口径**：
`trend` 由序列开头逐根累积，方向切换的那一根被标记 `is_breakout`。
信号因此**完全由 DataFrame 决定，无需反推初始状态**。

旧实现曾用 `first_run` 从 `df[_signal].dropna().iloc[-1]` 反推 `current_signal`，
但那条记录**就是正在处理的当前 K 线**，于是反推把本次突破设成"已发生"，
随后的同向去重把它吞掉 —— 进程重启后必丢一个突破信号。
v2 的累积计数不存在此问题，`first_run` 已删除。

注意 `alpha_trend_indicator` 会**就地往 DataFrame 追加指标列**（`alpha_value` /
`trend` / `is_breakout` / `ema200` / `*_entry` / `*_exit`），传入的是策略的活对象
`klines_df`，该副作用保留。

信号状态（去重方向）**留内存不落库**。本轮明确不引入 `signals` 表。
