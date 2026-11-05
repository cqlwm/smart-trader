# 编码设计

本文件沉淀跨模块的编码设计决策与约束（AGENTS.md 第三节要求）。
修改代码前先查阅本文档；设计变更时先更新本文档，再动代码。

## 一、总则

1. **记录模型用 Pydantic**：DB 行 → Record → Domain → Dict 的既有转换模式（见 AGENTS.md 第五节），
   禁止用 `dict[str, Any]` 承载业务语义；`extra="forbid"` 保持开启，字段拼写错误立即报错。
2. **易于测试**：外部依赖必须有内存实现（`tests/fakes.py`）；测试不触网、不落盘、相互独立。
   内存替身一律**显式继承**所替代的端口/接口 ABC（如 `FakeTradingPort(TradingPort)`），
   让 pyright 在类定义处校验替身签名漂移；唯一例外是 `FakeCcxtExchange` 显式继承
   `CcxtExchange` Protocol（第三方形状接缝，见 01 第二节类型准则）。
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

- 两张表**零外键、零关系、零对象图**，declarative ORM 的 relationship / unit-of-work / identity map
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
- `rowid` 排序（`load_active_positions` / `load_orders_for_positions` / `pending_orders` 的稳定 tie-break）
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

### 10. `TrendSpacingGate` 与 `GridSpacingGate` 的方向对称

两个间距闸门是同一判定式的镜像：

| 闸门 | 基准（极值层） | 判定 | 语义 |
| --- | --- | --- | --- |
| `GridSpacingGate` | 亏损方向极值（多头=最低价层） | `ratio <= -rate`（正值） | 逆势补仓 / 双向距离限制 |
| `TrendSpacingGate` | 盈利方向极值（多头=最高价层） | `ratio >= rate` | 顺势阶梯加仓 |

互斥原因：同一链内 AND 组合要求价格同时"低于最低层 − r₁"且"高于最高层 + r₂"，
堆栈有宽度时无解 —— 它们不是可组合的两个条件，而是同一条件（间距基准）的两种口径。
因此由 config 的 `trend_spacing_rate` 二选一装配（非空 → 顺势闸门替换网格闸门），
而不是提供两个可叠加的闸门。

**`TrendSpacingGate` 的回踩带与第二层特例（Step 29）**：
- 只有一层已成交仓位时，第二层**无条件放行**（逆势 / 顺势都加）——单层网格不存在
  "顺势阶梯"可言，先让第二层落地；
- 两层及以上，除"顺势间距"外，再按**价格高低**取最外层两层 `quantity > 0` 的仓位均价，
  收盘价落在 `[min, max]` 闭区间内也放行。它表达"价格在最外层两层开仓价之间的小回踩
  仍属顶部加仓区"——避免刚加的一层被反向信号立即剥掉的震荡磨损，同时让新层继续堆在
  价格顶部。回踩带只对至少两层已成交仓位生效。

示例：多头已有 `100, 101, 103, 106`，信号价 `105 ∈ [103, 106]` → 加一层 `105`，
持仓变为 `100, 101, 103, 105, 106`；只有一层 `100` 时，新价格 `99` → `99, 100`，
新价格 `101` → `100, 101`。

**优先级（先平后开）**：反向信号命中且浮亏层 ≥ 2 时，本根先批量止损，
`outcome.acted` 为真，回踩带不再加仓 —— "确认反转"优先于"区间内小回踩"；
回踩带只负责过滤"只有一层浮亏"的噪声场景（Step 29）。

### 11. `LayeredReducePolicy` 与 `ExitContext.positions`

- **为什么 `ExitContext` 需要仓位列表**：批量止损要统计"当前有多少层浮亏"并逐层产出
  决策，这是跨仓位判定；`EntryContext.positions` 早有先例（`MaxPositionsGate` /
  `GridSpacingGate` / `TrendSpacingGate` 都是跨仓位判定），两个上下文保持对称，同为纯数据。
- **至少两层浮亏才触发、一次剥完全部浮亏层怎么保证**：`evaluate` 每次按全仓统计
  `quantity > 0 且 ratio < 0` 的层；`len(losing) < 2` 直接不产出决策，否则对
  `losing` 中的每一层返回 `should_exit`。策略逐仓位询问策略链，因此同一根完成 K 线
  会一次性为所有浮亏层挂出（或 force 重挂）出场单，不需要额外的节流状态或计数器。
  单层浮亏不触发 —— 震荡中的单层小亏是噪声，触发只会制造磨损（Step 29）。
- **为什么 `force=True`**：逆势减仓必须实际减仓。普通止盈可以"挂价等回撤"，但下跌
  趋势中挂着不成交会让逐层推进停滞；force 让每根完成 K 线撤单重挂到最新收盘价
  （与 `StopLossPolicy` 同属"必须执行"类）。
- **为什么按盈亏状态分派（Step 24）**：信号止盈与信号止损是同一次"反向信号
  离场"的两个分支，区分维度是**该层当前的盈亏状态**而非两套独立策略 ——
  浮盈（`ratio > 0`）走止盈（非 force，保留原挂单），浮亏（`ratio < 0`）走止损
  （至少两层浮亏才触发，全部浮亏层 force 撤单重挂）。两分支按盈亏符号天然互斥，同层永不冲突；
  持平（`ratio == 0`，含均价不可用）不触发。这取代了 Step 23 的"两个独立策略
  可并存"形态 —— 那会让盈利的最外层被 force 立即剥掉，违背"止盈/止损"的
  字面语义（止盈 = 有浮盈才走，止损 = 有浮亏才走）。
- **为什么删除 `exit_signal_take_profit_min_rate`**：盈亏分派后止盈的条件就是
  "有浮盈"，任何额外门槛都会再造一个"看着是活参数、实际改了也不生效"的
  歧义字段（同 `stop_loss_rate` 教训）。仓位行上的 `signal_min_take_profit_rate`
  列保留（schema 兼容，`profit_level` 档位 1 不再被任何策略消费）。
- **不要求盈利 → 要求浮亏**：`LayeredReducePolicy` 只处理 `ratio < 0` 的层 ——
  止损的字面语义；盈利层由止盈分支（或固定止盈）处理，不再被止损 force 剥掉。
  在此基础上再加"至少两层"门槛，把单层噪声挡在止损之外（Step 29）。

### 12. `PositionLifecycle` 入场单结算的职责拆分（Step 21）

**背景缺陷**：`_on_kline`（盘中路径）把"入场单结算"与"休息止盈单挂单"用同一守卫
（`resting_take_profit is None → return`）短路 —— 未开启限价止盈的配置
（如 `dogeusdt_trend_long`，`take_profit_use_limit_order=False`）下入场单成交**永远
无人结算**：`position.quantity` 恒为 0、账本行停在 `open`，止盈/止损/间距全部失真。
实测事故：入场单 14:38 成交，进程存活且正常开第二仓，账本直至进程死亡仍为 `open`。

**拆分结果**（两个职责各自独立，互不依附）：

| 方法 | 职责 | 调用方 | 对未成交入场单 |
| --- | --- | --- | --- |
| `settle_entry_fills(position)` | 只查询并回写成交事实 | `_on_kline` 每 tick 无条件 | **不撤单**，留着等成交 |
| `settle_pending_entries(position)` | 撤剩余入场单 + 结算事实，返回仓位是否归零 | `check_close_position`（出场流程） | 撤单复查后按终态结算 |

**为什么盘中结算"只 settle 不撤单"**：入场单由追单器刻意挂出（GTX 逐档递进），
盘中撤掉等于推翻追单成果；撤单语义（"防继续建仓"）只在离场决策成立时才合法。
旧盘中路径复用 `settle_pending_entries`，要么（限价止盈开启）刚挂的入场单
一秒内被撤、要么（未开启）永不结算 —— 两个方向都是错的，根因是职责耦合。

**查询不到（None）的容错语义**：与出场单跟踪同源 —— 距创建过近（< 5s，
同 `OrderReconciler.min_age_seconds` 口径，防交易所 settle 延迟）先跳过；
足够老才按 `EXPIRED` 结算（交易所从未存在该单）。查询异常记日志跳过，
下一根再试。内存行的 `created_at` 为空（运行中刚下的单）按"过近"保守跳过。

**`_on_kline` 的新结构**：`latest_kline` 缺失仍整体返回；逐仓位先无条件
`settle_entry_fills`；休息止盈块（出场单触及查询 + 挂休息止盈单）整体
以 `_resting_take_profit is None → continue` 守卫 —— 该守卫此后只表达
"限价止盈未启用，无休息单可跟踪"，不再连带吞掉入场单结算。

### 13. `StopLossPolicy` 固定止损与跟踪止损的语义拆分（Step 22）

**背景缺陷**：`_open_position` 开仓时把 `stop_loss_rate` 一次性算成
`trailing_stop_price` 快照，`StopLossPolicy` 也只读该字段 —— 字段看着是活参数，
实际是死快照：修改 config 的 `order_stop_loss_rate` 或库里的 `stop_loss_rate`
对存量仓位永远无效（实测事故：费率 0.05 → 0.005 + 重启，触发价仍停在
开仓价 × 0.95）。

**拆分后的判定口径**（`enable_stop_loss` 仍是总开关）：

| 模式 | 触发价来源 | 语义 |
| --- | --- | --- |
| 固定止损（`enable_trailing_stop=False`） | `仓位均价 × (1 ∓ stop_loss_rate)` 实时计算 | **活参数**：改库或改配置后重启即生效 |
| 跟踪止损（`enable_trailing_stop=True`） | ratchet 字段 `trailing_stop_price`（缺失不触发） | 播种 = `开仓收盘价 × (1 ∓ stop_loss_rate)`，每根 K 线只向盈利方向抬升 |

**为什么固定止损锚定 `position.price`（仓位均价）而不是开仓收盘价**：均价才是
"这层仓位真正的成本"，止盈口径（`profit_level` → `price`）已经用它；两口径同源
（费率语义对称：止损 -rate、止盈 +rate），改费率立即生效的心智模型才成立。
单层成交时均价 ≈ 开仓收盘价，行为与旧实现几乎重合。

**为什么 `_open_position` 不再为纯固定止损写 `trailing_stop_price`**：该字段从此
只归属跟踪止损（ratchet 运行时状态，必须落库的原因不变）；为固定止损写快照
正是本次缺陷的根源 —— 死数据会继续误导下一个改参数的人。

### 14. `ExitContext.close` 必填与 `_exit_context` 的无行情契约（Step 25）

**背景缺陷**：`ExitContext.close` 带 `= 0.0` 默认值，`_exit_context` 在
`kline is None` 时静默兜底 `close=0.0`。该兜底是危险数据而非中性值：
离场策略会把 0.0 当真实价格判定 —— LONG 仓被判定为暴跌穿止损（force 离场）、
SHORT 仓被判定为巨额浮盈（触发止盈），全部按 0.0 定价静默产生垃圾决策。
且与入场侧不对称：`EntryContext.require_close()` 对 `close=None` 抛
`SmartTraderError(MARKET_DATA_UNAVAILABLE)`，出场侧却默默兜底。

**修复后的契约**（两层）：

| 层 | 契约 | 语义 |
| --- | --- | --- |
| `ExitContext.close` | 必填字段，无默认值 | 模型层直接拒绝无价构造（`ValidationError`） |
| `_exit_context` | `kline is None` 抛 `SmartTraderError(MARKET_DATA_UNAVAILABLE)` | 与入场侧同一异常码，给未来调用者的显式契约 |

**`check_close_position` 的前置守卫保留**：启动初期无行情时优雅返回空
`CloseOutcome()` 属正常流程控制（不崩主循环）；`_exit_context` 的 raise
只保护绕过守卫的未来调用点。调用图上 `_exit_context` 仅由
`check_close_position` 调用，守卫先于构造，正常运行永不触发 raise。

### 15. 交易所响应解析的严格契约与"缺失 ≠ 零值"（Step 26）

**背景缺陷**：`_to_float` 把"空值与 0"统一归一化为 None，丢失"缺失 vs 零"的
区别；调用点再用 `or 0.0` 找回零值，同时把"缺失"也变成 0.0 —— 与同函数内
`clientOrderId`/`side` 缺失即抛 `ORDER_RESPONSE_INVALID` 的严格口径矛盾。
下游 `settle_order` 的 `result.quantity > 0 else order.quantity` 兜底恰好被
这个 0.0 触发，两层静默互相掩护，账面看不出交易所响应异常。

**修复后的契约**：

| 字段 | 缺失处理 | 语义 |
| --- | --- | --- |
| `amount`（委托量） | 抛 `ORDER_RESPONSE_INVALID` | 币安 fapi 下单/查单/撤单响应必回传 origQty |
| `filled`（成交量） | `None → 0.0` | 未回传视为无成交；完全成交另有兜底（filled=quantity） |
| `price` / `average` | 0 与缺失都归 None | 市价单无委托价 / 未成交无均价，None 表示"未提供" |
| 持仓 `contracts` / `entryPrice` | 有方向但缺失 → 丢弃整行 + warning | 不得以 0.0 伪装数量/成本价；无方向占位行静默跳过 |

**`Position.entry_price` 随之必填**（删除 `= 0.0` 默认值）：0.0 是
"看起来合法实则错误"的成本价，模型层直接拒绝无价构造。当前无消费者，
属预防性收紧 —— 一旦将来有 PnL 逻辑读它，0.0 伪装就是 `ExitContext.close`
同款事故。

**`OrderReconciler._too_fresh` 与 `PositionLifecycle` 真正同口径**：
created_at 为空（年龄未知）→ 保守跳过。对账"查不到即删行"，若把"年龄未知"
当"足够老"，刚预写的订单会被误删。DB 行因 journal 首写兜底总有 created_at，
此修复防的是未来绕过 `_values` 的写入路径（如对账器扩展到 open 状态）。

**`JournaledTradingPort` 的 `grid_position_id or ""` 已修复（推翻了原审计结论）**：
原判断"`__init__` 已有 XOR 契约校验，`or ""` 只是模板路径的瞬时占位"**不成立**
—— XOR 只校验 `is None`，`grid_position_id=""` 能穿过（`(position is None) ==
(grid_position_id is None)` 求值为 `False`），随后 `or ""` 把空串原样吞掉，
`append_order` 落库一行 `grid_position_id=""`。该行在
`OrderJournal.load_orders_for_positions` 的 `in_(position_ids)` 过滤下**永远查不
回来**：交易所挂着活单，账本无法归因到任何仓位，仓位数量永不反映这笔成交 ——
静默数据损坏而非响亮失败。`orders.grid_position_id` 是 `nullable=False` 但无外键，
数据库层不兜底；`GridPositionRow.id` / `GridPosition.id` 默认值均为 `""`，非纯理论风险。

修复三处（同一根因：`or` 把"缺失"与"空"混为一谈）：

| 位置 | 修复 |
| --- | --- |
| `__init__` | 空串仓位号 → `ValueError`，与既有 XOR 校验同处，响亮拒绝 |
| `_ensure_row` | 按 `position` / 追加两分支**显式**取仓位号，删除 `or ""`；提取 `_new_record` 消除两分支 7 字段重复 |
| `_record_from_result` | 删除 `or result.custom_id` 与 `or ""` 两处**死代码**（`_ensure_row` 先行后两字段必然非 None），改显式校验 |

`_record_from_result` 的 `or result.custom_id` 危害高于空串：真走到该 fallback 会
用非预写的 `order_id` 调 `settle_order`，SQL `WHERE` 匹配 0 行且**不报错**，成交事实
静默丢失。这与 `_exit_context` 对 `kline is None` 抛 `MARKET_DATA_UNAVAILABLE`、
`_to_float` 拒绝"空值伪装 0"（第 284-317 行）同源：`or` 兜底把错误转成静默脏数据。

**数据一致性校验一律不用 `assert`（`python -O` 会剥离它）**：`assert` 是语句而非
函数调用，解释器在**编译期**于 `-O` / `-OO` / `PYTHONOPTIMIZE=1` 下整段丢弃含 assert
的代码块，运行时零检查。当前 `Dockerfile:28` 是 `uv run python run.py`（无 `-O`，
全仓库无 `PYTHONOPTIMIZE`），所以 assert **目前**生效 —— 但这依赖启动命令而非代码，
任何人加个 `-O` 就静默失效，数据校验凭空消失，退回"静默脏数据"。这与
`EntryContext.require_close` 的既有裁决同源（`entry_gate.py:35` 明写
`@note 不用 assert: python -O 下 assert 会被剥离`），本模块一度与之相反，现已统一。

第二层理由：`AssertionError` 不在统一异常体系内，策略层
`except SmartTraderError` 捕不到，会穿透到主循环；AGENTS.md 第四节要求"执行失败
不能导致服务崩溃，必须捕获异常并记录日志"，assert 崩的正是没人接的那条路径。

`JournaledTradingPort` 的四处标识校验（`place` 发单前、`_ensure_row` 追加分支、
`_record_from_result` 的订单号与仓位号）统一抛 `SmartTraderError(ORDER_NOT_PLACED)` ——
复用既有异常码而非新增枚举：语义即"下单未能完成"，且新增枚举成员会让每个 `ErrorCode`
消费方的映射表多一处待维护。异常文案区分三种触发点（拒发 / 无法归属 / 无法回写），
便于日志定位。

**这些校验用 `if ... raise` 而非 assert 后，测试才能真正验证它们**：assert 分支在
`python -O` 下不可测，而 `pytest.raises(SmartTraderError)` 在任何优化级别下都成立 ——
这是"校验必须可测"的技术前提。四个测试用受控钩子（替换 `trading.place` /
`placer._ensure_row`）在精确时机抽掉内部标识，逐一验证：拒发时交易所零请求、
校验失败不留预写行、回写失败保留行待对账。

### 16. 持仓对账：外部平仓的本地收敛（Step 27）

**背景缺陷**：本地派生量（`quantity = opening + Σentry_filled − Σexit_filled`）
只认订单流水，用户在交易所手动平仓后本地不知道 —— 策略按本地数量挂出的平仓单
被交易所整单拒绝（对冲模式 -2022），且 `Task.safe_run` 吞掉异常、下一根 K 线
原样重试，陷入"拒单 → 吞异常 → 重试"死循环：仓位永远平不掉、偏差永不收敛、
error 日志刷屏。对账缺口只靠 `OrderReconciler`（订单行级）无法覆盖 —— 它对账
"订单存在性"，不对账"持仓数量"。

**设计**（`strategy/position_reconciler.py`，每根完成 K 线 + 启动时各跑一轮）：

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 对账基准 | 交易所实际持仓为唯一收敛权威（`AccountPort.positions`，按 `position_side` 过滤） | 用户场景就是"交易所是事实，账本过时" |
| 缺口分摊 | LIFO —— 最外层优先（多头=盈利方向最高价层，空头=最低价层） | 对账分摊顺序单一心智模型；避免与减仓策略的具体选择逻辑耦合 |
| 被全吃的仓位 | 先撤挂单结算事实（`force_settle_exit` / `settle_pending_entries`）再内部归档，**绝不下对冲单** | 交易所已无对应持仓，下对冲单会被拒或反向开仓 |
| 部分被吃的仓位 | 记录外部扣减量 `adjustments[id]`，平仓/休息止盈下单量 clamp 为 `quantity - 扣减量` | 行为正确优先于账本好看；扣减量每轮对账全量重算，无过期状态 |
| 反向偏差（交易所 > 本地） | 仅告警，不自动收编 | 手动加仓/来源不明仓位不能被策略静默接管（10-12 遗留的来源不明 SHORT 即此类） |
| 账本回写 | **不回写**：`orders` 行只保存订单事实，外部平仓成交价本地拿不到，不伪装记账 | 与三.15"缺失 ≠ 零值"同源：本地不知道的事实就承认不知道，归档仓位保留原始订单行 + 日志留痕 |
| 容错 | 查询异常跳过本轮、撤单异常单仓容忍，均下轮重试 | 对账是收敛手段不是裁决，绝不中断主循环 |
| 浮点容差 | `_QTY_EPS = 1e-6` | 数量经 `step_size` 格式化，最小步长远大于 1e-6，容差只吸收浮点噪声 |

**两个时序细节**：

1. **盘中挂休息止盈单前实时对账**：完成 K 线对账只在边界跑，而入场单成交结算
   后的第一次盘中 tick 就可能挂休息止盈单 —— 若用上一边界的对账结果，外部在
   周期内平掉的量 clamp 不到。挂单是每仓位生命周期一次的低频动作，挂单前对账
   一次的请求成本可接受；挂单量错误则是一张注定被拒的单。
2. **对账归档当根不开新仓**：归档是敞口减少动作，与平仓动作同类 ——
   `_on_kline_finished` 的开仓守卫从 `not outcome.acted` 扩展为
   `not outcome.acted and not archived`。

**启动对账**：策略构造内 `load_positions` 之后立即跑一轮（构造即对账，与
`OrderReconciler` 的"构造内对账"先例一致）—— 否则重启后到首根 K 线之间存在
一个对账盲区，而"重启后仓位已被手动清掉"恰是高频场景。

**测试基座的镜像端口**（`LedgerMirroringAccountPort`）：存量测试的隐含前提是
"本地 = 交易所"，默认账户端口必须表达这个前提。镜像绑定**账本**而非策略内存 ——
策略构造期的启动对账发生在 `load_positions` 之后，彼时账本是唯一可用的仓位来源
（lambda 捕获 build 局部 `strategy` 变量会触发 free-variable NameError，因为
构造尚未返回）；运行期账本与内存同步落库，两者一致。需要模拟外部平仓偏差的
测试显式传 `FakeAccountPort`。集成档 `StatefulFakeExchange` 同时实现 `AccountPort`：
净持仓 = 簿内 BUY 成交 − SELL 成交 − `external_close()` 脚本扣减，与真实交易所
"订单成交产生持仓"的因果关系一致，`external_close` 即"用户手动平仓"的脚本入口。

## 四、数据事件循环设计（data_event_loop / task）

### 1. 载荷统一为 Kline

- `Task.run(kline: Kline)` / `DataEventLoop.loop(kline: Kline)`：事件载荷是领域对象，
  不是原始传输文本。ws 字符串 → Kline 的翻译只发生在 `BinanceDataEventLoop.on_message`
  一处（`parse_kline_message` 纯函数，属传输细节，放在 `data_event_loop.py`，
  避免 task 层与循环层的循环导入）；`StrategyTask.run` 只做 `symbol + timeframe`
  过滤后转调 `strategy.run(kline)`。
- 定时拉取型 `TimerDataEventLoop` 天然产出 `Kline`，与 ws 路径共用同一 fan-out
  （executor + `Task.safe_run` 异常隔离），不再有第二套分发契约。

### 2. TimerDataEventLoop 的可测试性设计

- **时钟注入**：`clock: Callable[[], float]`（默认 `time.time`），测试用假时钟推进，
  无真实长 sleep；
- **`_next_events(now)` 是纯函数**：输入当前时刻，输出最早到期时刻及该时刻**并列到期**
  的全部事件 `(fire_at, events)`（如 1m 与 5m 边界重合必须同刻齐发，否则后到的时间框架
  会被跳过）；无线程、无 IO，全部调度数学在此单测；
- **`_wait_until(target)` 分片等待**：小片 `threading.Event.wait` 轮询时钟，
  `stop()` 经 `_wakeup` 立即唤醒并返回 False；`start()` 阻塞语义与
  `BinanceDataEventLoop.start()` 一致（入口线程驱动，KeyboardInterrupt → stop）；
- **数据源注入**：`market: MarketDataPort`，测试复用 `FakeMarketDataPort`。

### 3. 调度语义（Step 15）

- 边界事件 = 周期边界 + `fetch_delay`（默认 2s，必须 < tf/2，构造时校验）：
  `fetch_ohlcv(limit=2)` 取最后一根 `finished=True`，即"最后一根已完成K线"→
  经 `strategy.run(kline)` 触发 `on_kline_finished`；
- **不等待重试**：无已完成K线（本地时钟落后）或拉取失败 → 记日志、本周期不分发，
  由下一个边界自然补上——重试只能拯救"本地时钟落后 2~3s"这种几乎不存在的场景，
  却引入等待机器；去重（按 `(symbol, timeframe)` 记录最近分发的时间戳，**已分发过的
  同一根K线返回 None**）才是漂移场景的正解：既不重复分发，数据也只迟到不丢失；
- **陈旧拒绝**（边界事件）：`expected_open_ms - kline.timestamp > tf_ms` → 判定数据源
  异常，记 error 拒绝分发。容忍带是"恰好落后一个周期"（交易所滚出稍慢，重复由去重
  兜底）与"超前"（本地时钟落后，取最新已完成K线）；被拒的Kline **不写入去重记录**，
  否则边界追上该时间戳时会被误判为"已分发"。实例：Step 17 之前适配器把 `limit`
  位置传进 ccxt `fetch_ohlcv` 的 `since` 槽（`startTime≈1970`），恒拉数据集最老 500 根
  K线，若无此规则，2020 年的垃圾K线会在首次出现时绕过去重进入策略
  （WS 模式的历史初始化同样受累）；
- 中点事件 = 周期 1/2 处：`fetch_ohlcv(limit=1)` 取进行中 K 线 → 触发 `on_kline`；
- 错过的事件跳过（只调度未来）；同一周期至多分发一次；
- fetch 抛异常 → 记日志跳过本周期，循环不中断（与第五节异常策略一致）。

## 五、只读 API 与 Web 控制台（P0）

### 1. 读写分离：`OrderJournal` / `OrderJournalReader`

- `OrderJournal`（写仓储）保持"打开即建表 + 校验结构 + 写方法"；
- `OrderJournalReader`（只读仓储）**不建表、不暴露任何写方法**，构造时校验库文件存在与
  结构版本（缺列时与 `OrderJournal` 报同样的 ALTER 提示），之后只发 SELECT；
- 两者共用 `storage/queries.py` 的 SELECT 构造与 `storage/engine.py` 的连接 / 结构校验，
  避免"读路径复制一份查询真相"；
- 只读连接仍由 `_on_connect` 设置 `busy_timeout`，与策略进程的 WAL 写并发共存；
  API 不设置 `journal_mode` 之外的结构性 PRAGMA，也不执行 `create_all`。

### 2. 分层

```
web/ (React)  ──HTTP──▶  smart_trader/api/  ──▶  storage.reader  ──▶  sqlite (只读)
                                │
                                └──▶  strategy.grid_position（只读派生属性）
```

- `api/schemas.py`：响应契约（Pydantic，`extra="forbid"`），与 `records.py` 解耦；
- `api/service.py`：聚合与派生，**只调用 `GridPosition` 的计算属性**，不重写数量/均价/盈亏公式；
- `api/app.py`：路由 + 依赖注入（app 构造时传入 reader），路由函数保持薄层；
- 禁止 `api` import `OrderJournal` 写方法、ccxt、execution；`web` 不直连数据库。

### 3. 可测试性

- `create_app(reader, ...)` 接受已构造的 reader：测试用 `tmp_path` 文件库播种后构造 reader，
  不启动真实网络；
- service 层是纯函数（reader → view），路由层只做参数解析与 404 映射；
- 只读契约由测试锁定：路由表无写方法、`POST /api/orders` 返回 405、缺失库文件构造即报错。

### 4. 前端约定

- `web/`：React + TypeScript + Vite + TanStack Query；HashRouter 避免 SPA history fallback；
- 开发期 Vite proxy `/api` → `127.0.0.1:8000`，生产期 `npm run build` 后由 API 托管 `web/dist`；
- 所有金额/数量展示保留原始精度，不做前端二次计算（派生量以 API 为准）；
- P0 只读页面不提供写操作按钮；P1 控制按钮只提交命令（不直接下单 / 不写账本），
  并且只有危险动作（全平 / 撤入场单 / kill switch）弹二次确认。

## 六、P1 控制面设计

### 1. 为什么是命令表而不是 API 直接调策略

控制台路由与策略运行时虽同进程，仍不直接调用策略对象：直接调用会绕过审计与幂等，
并把 HTTP 请求生命周期绑定到策略回调锁（`cancel_entries` 必须持 `_callback_lock` 撤单）。
`commands` 表是天然的解耦层：提交是普通 INSERT，执行由 `CommandRunner` 轮询认领，
天然支持审计、幂等与重启后补执行；即使未来再拆进程也无需改动。

### 2. 唯一写者与动作边界

- `CommandStore` 只写 `commands`，不得 import / 调用 `OrderJournal` 写方法；
- 控制动作只调用 `ControllableStrategy` 的四个方法，不直接调 `TradingPort` / `Journal`；
- `pause` / `resume` / `request_close_all` 只改内存标志（bool 写入原子），
  无需持锁；`cancel_entries` 会撤单并改账本，必须持 `_callback_lock`；
- `close_all` 在 `is_running=False` 时仍由 `_on_kline_finished` 消费，
  这是 kill switch "先全平再暂停"的语义基础（`if self.is_running or self.close_position:`）。

### 3. 认领与幂等

- 认领：事务内 `SELECT ... WHERE status='pending' ORDER BY requested_at LIMIT 1` 后，
  用 `UPDATE ... WHERE id=:id AND status='pending'`；`rowcount != 1` 表示被其它执行者抢走；
- 完成：`UPDATE ... WHERE id=:id AND status='running'`，避免重复写终态；
- 提交幂等：`idempotency_key` 唯一索引；`IntegrityError` 时回读已有行返回，
  前端双击不会产生两条命令。

### 4. 运行态心跳（非账本状态）

- 账本只记录事实, 不记录"进程是否存活 / 策略是否暂停"这类易变状态; 若把心跳写进
  `grid_positions` 会污染逻辑仓位语义（重启时会被 `load_positions` 恢复成幽灵仓位）。
- 因此用 `.data/runtime.json` 心跳文件: `CommandRunner` 每轮原子替换写入, 控制台只读;
  文件缺失/损坏/过期一律降级为"离线", 不影响账本与命令执行。控制台路由不持有策略对象,
  心跳文件同时保留未来拆进程与外部监控的接缝。
- 15s 新鲜度阈值（心跳每 2s）容忍短时 IO 抖动; 正常退出 `CommandRunner.stop()` 删除文件。
- 只读侧合并规则: 心跳新鲜时用其 `symbol` / `position_side` / `timeframe` / `is_running`
  覆盖账本摘要的展示字段; 心跳过期时不覆盖, 避免离线进程的旧信息冒充当前状态。
- 该机制让"尚未写账本的运行中策略"出现在策略列表并可被控制, 同时让历史离线策略的
  控制按钮置灰。

### 5. 测试策略

- `CommandStore` 用临时文件库：状态流转、并发认领只成功一次、幂等回读、缺表报迁移提示；
- `RuntimeRegistry`：原子写入、暂停状态、清理、缺失/损坏容忍；API 合并运行态与 15s 过期降级；
- 策略控制用现有 fake 装配：暂停拦截入场、恢复放行、暂停下全平仍执行、撤入场单归档空仓位；
- `CommandRunner` 用 `FakeControllableStrategy`（显式继承 `ControllableStrategy`）与内存/文件 `CommandStore`，
  覆盖成功、未知策略、执行异常、kill switch 全量下发；
- API 用 `TestClient` 锁写面：POST 只出现在命令提交路由，参数非法 422，命令列表过滤与分页。

### 6. 对账可观测：暴露事实，不自动处置

`PositionReconciler` 只把每一轮成功对账的 `exchange_qty` / `local_qty` / `gap` 存入内存快照，
`PositionLifecycle` 只统计出场单连续查询失败次数；两者经 `runtime_info()` 进入心跳文件，
API 派生 `ReconcileView` 与告警。这里刻意**不做**自动收编 / 自动平掉外部仓位：

- 外部仓位的开仓价、意图、所属策略都未知，自动收编会污染网格层级与止损线；
- 自动平仓是在 API/策略未授权的情况下主动交易，风险高于"告警 + 人工处置"；
- 可观测数据足以让操作者判断是手工仓位还是账本漂移，下一步再单独设计处置命令。

对账查询不再以"本地有仓位"为前提：本地为零、交易所非零同样要暴露，
否则外部单独开仓会被静默忽略。出场单查询失败仍然保留待跟踪（绝不重复下单），
只是把连续失败次数升级为可见告警。

### 7. 人工仓位声明：可审计的告警抑制

`position_acks` 是 **API-only 的操作者声明**，策略进程不读取：

- 策略不需要知道"这 3000 是人工的"来决策，它仍只管理自己的 500；
- 声明只参与 API 的告警派生，因此不需要让策略进程引入新的数据库读取；
- 新建声明软撤销旧声明（`status=revoked`），撤销幂等且保留历史，保证审计可查。

为什么按 `surplus = exchange_qty - local_qty` 判定，而不是比较绝对差额：

- 支持部分声明（标记 2000、剩余 1000 继续告警）；
- 策略自身开平仓会同时改变 `exchange_qty` 与 `local_qty`，`surplus` 保持不变时声明仍然有效；
- 人工仓位变化会改变 `surplus`，声明自动失效并重新告警，避免旧标记掩盖新事实。

声明数量不能大于当前差额（创建时校验），创建后事实变化导致超额则 `position_ack_stale`。
仍不提供自动收编 / 自动平仓：声明只解决告警噪音，不改变仓位归属。

## 七、P2 行情设计

### 1. 数据源：行情-only 适配器

`BinanceExchange` 要求私有 + 公共两个 ccxt 实例；P2 只需要公共行情。把
`symbol_info / latest_price / fetch_ohlcv / _parse_timeframe_ms` 抽到 `_MarketDataMixin`，
完整适配器与新增的 `BinanceMarketData`（显式继承 `MarketDataPort`）共用同一份翻译逻辑。
`create_public_market_data()` 只构造公共 ccxt 实例，零凭据；测试用 `isinstance` 锁定它
不携带 `TradingPort` / `AccountPort` 能力。

### 2. 惰性初始化与故障隔离

`create_public_market_data()` 构造即 `load_markets`（可能耗时或超时）。API 用
`MarketDataProvider` 惰性获取：首次 `/api/market/chart` 才初始化，失败返回 503，
不拖垮总览/持仓/订单/控制台页面。成功一次后缓存复用；前端对失败提供重试。

### 3. 指标单一实现

`market_service.build_market_chart` 直接调用策略层 `alpha_trend_indicator`，
不复制 ATR/MFI/棘轮公式；K 线行与指标行按索引一一对齐。`direction` 由最后 `trend`
符号派生，**不冒充**策略的翻转事件信号。

### 4. 图表渲染

- lightweight-charts v5：`addSeries(CandlestickSeries/LineSeries)`、`createSeriesMarkers`、
  `series.createPriceLine`；时间统一用秒级 `UTCTimestamp`。
- 订单标记按当前 timeframe 归并到 K 线时间桶；只有时间桶存在于已加载 K 线时才添加，
  否则标记会被静默丢弃。
- 价格线只叠加当前交易对的活跃仓位（最多 10 层），入场蓝虚线 / 止损红实线 / 止盈绿虚线。
- 价格轴精度按最新价动态选择（`ceil(-log10(price)) + 4`，2–8 位），避免小币种被显示成 `0.08`。
- 轮询刷新用 `setData` 并恢复可见逻辑区间，避免每 10s 把用户视野重置。

## 八、P3 配置设计

### 1. 为什么不做热替换

`SignalGridStrategy` 实例持有 `_callback_lock`、已加载的活跃仓位、在途出场单与信号状态。
运行中换成新配置对象会丢失这些内存状态并把开放仓位变成孤儿；因此配置只在进程启动时读取，
保存后由前端明确提示"重启策略进程生效"。这是有意的运维语义，不是功能缺口。

### 2. payload 与 `Signal` 的序列化边界

`SignalGridStrategyConfig` 含 `Signal` 抽象接口（非 Pydantic 可序列化对象），不能直接落 JSON。
`StrategyConfigPayload` 把可序列化字段单列，用 `signal_type` + `atr_multiple` + `period`
描述信号，`to_strategy_config()` 在加载时物化实例；`from_strategy_config()` 反向转换只给
种子脚本用。存储层因此不需要 import 策略层：`ConfigStore` 收发的 `payload` 是 JSON 文本
（与 `CommandRecord.params` 同口径），解析与校验统一在 `StrategyConfigPayload`。

### 3. 乐观锁

`update` 用 `UPDATE ... WHERE strategy_key=? AND version=?`，影响 0 行时区分"不存在"与
"版本过期"并抛不同异常；API 映射 404 / 409。前端 PUT 必带 `expected_version`，
两个会话同时编辑时后提交者得到明确冲突而不是静默覆盖。

### 4. 启动装配

`build_strategy_tasks(records, data_source=...)` 是配置行与策略实例的唯一汇合点：
过滤 `enabled` 与数据源，构造 `SignalGridStrategy` 与 `StrategyTask`。统一入口
`run.py` 按每个配置行的 `data_source` 分别调用它（WS / Timer 循环可同时存在），
不再 import 具体策略模板；模板只保留 `*_config()` 构造函数供种子脚本使用。
空集合返回空列表：统一入口以控制台为宿主，零策略仍须能配置与查看；
"只读模式"告警由 `StrategyRuntime` 负责。

### 5. 测试策略

- payload：JSON 往返、两种信号物化、模板转换往返、非法交易对 / 未知字段拒绝；
- store：版本递增、陈旧版本冲突、重复键、缺表迁移提示、列表排序稳定；
- loader：数据源 / 启用过滤、键注入、空集合返回空列表、顺序稳定；
- API：只写 `strategy_configs`（写面断言更新为 GET/POST/PUT），404 / 409 / 422 映射，
  活跃仓位护栏（禁用与身份字段），token 鉴权；
- 前端构建通过，浏览器验收列表 / 编辑 / 停用冲突提示。
