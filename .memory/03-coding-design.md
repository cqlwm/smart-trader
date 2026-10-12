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

### 11. `LayeredReducePolicy` 与 `ExitContext.positions`

- **为什么 `ExitContext` 需要仓位列表**：逐层减仓要回答"我是不是最外层"，这是跨仓位
  判定；`EntryContext.positions` 早有先例（`MaxPositionsGate` / `GridSpacingGate` /
  `TrendSpacingGate` 都是跨仓位判定），两个上下文保持对称，同为纯数据。
- **一次只剥一层怎么保证**：策略逐仓位询问策略链，只有最外层返回 `should_exit`；
  该层已有未成交出场单时按 `force` 语义撤单重挂，而此时下一层仍不是最外层 ——
  自然等待，不需要额外的节流状态或计数器。
- **为什么 `force=True`**：逆势减仓必须实际减仓。普通止盈可以"挂价等回撤"，但下跌
  趋势中挂着不成交会让逐层推进停滞；force 让每根完成 K 线撤单重挂到最新收盘价
  （与 `StopLossPolicy` 同属"必须执行"类）。
- **为什么与 `SignalExitPolicy` 二选一**：两者都表达"信号命中 → 该层该走"，同时装配
  会在反向当根既逐层剥又全平所有盈利层，语义互相拆台。`enable_exit_signal` 保持为
  信号离场的总开关，`enable_layered_reduce` 只是口味开关。
- **不要求盈利**：与 `SignalExitPolicy` 的止盈语义不同，逆势减仓是去风险动作，
  亏损也减；该差异由 `tests/test_exit_policy.py` 的 `test_layered_reduce_*` 锁定。

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
