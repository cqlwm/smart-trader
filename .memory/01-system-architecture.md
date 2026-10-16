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
        │      ports (smart_trader/exchange/ports.py)   零实现、零第三方依赖
        │          ▲
        │          │  适配器实现端口
        └──────────┴── adapters (smart_trader/exchange/binance/binance_exchange.py)
                           ccxt / REST 细节只存在于这里
```

依赖规则（强制）：

1. **业务层不得 import ccxt 或任何交易所 SDK 概念**（`setup_*`、raw 交易所对象等）；需要行情/交易/账户能力时只依赖 `smart_trader.exchange.ports` 的端口。raw ccxt 实例（`CcxtExchange` 的实现对象）不得越过适配器边界。
2. **适配器只做翻译**：领域对象 ⇄ 交易所参数/响应。不做重试编排、不做下单策略、不碰数据库。
3. **领域对象是唯一契约**：跨层传递的订单/持仓必须是 `OrderRequest` / `OrderResult` / `Position`，禁止使用 `dict[str, Any]` 承载订单语义。
4. 交易所异常在适配器内统一翻译为 `SmartTraderError` + `ErrorCode`，业务层不感知 ccxt 异常类型。
5. **storage 是纯数据层**：只做 sqlite 存取与事务，只依赖 `model` 与 SQLAlchemy（SQLAlchemy 仅限 storage 内部，对外只暴露 Pydantic 记录与 `OrderJournal`）；不 import ccxt、不依赖 execution / strategy。storage 之外的任何层不得 import sqlalchemy。
6. **所有下单路径必须经过 `JournaledTradingPort`**（策略常规单、追单的每次尝试）：id 预留与记账不可绕过，否则最终一致性失效（见第十一节）。

## 二、端口定义

`smart_trader/exchange/ports.py`，全部为 `abc.ABC` + `@abstractmethod`，按调用方需求拆分（接口隔离）：

| 端口 | 职责 | 方法 |
| --- | --- | --- |
| `MarketDataPort` | 只读行情与交易规则 | `symbol_info` / `latest_price` / `fetch_ohlcv` |
| `TradingPort` | 下单与订单查询 | `place` / `query` / `cancel` |
| `AccountPort` | 账户与持仓 | `balance` / `positions` |
| `Exchange` | 三端口组合接口（`Exchange(MarketDataPort, TradingPort, AccountPort, ABC)`） | 供装配层（bootstrap / template / demo）持有完整交易所能力 |

调用方按需注入，例如 `SignalGridStrategy` 只接受 `trading` + `market`，不持有账户能力。

**实现与替身一律显式继承端口**：`BinanceExchange(Exchange)`、测试侧 `FakeTradingPort(TradingPort)` / `FakeMarketDataPort(MarketDataPort)` / `StatefulFakeExchange(TradingPort)`。缺方法在实例化时立即报错，pyright 在类定义处校验签名（含测试替身，防 Fake 漂移掩盖适配器缺陷——Step 17 教训）。

**类型准则（Protocol vs ABC）**：
- **内部接口一律 `abc.ABC` + 显式继承**——实现都是我们自己的代码，层次可见、实例化强制完整、isinstance 可用。
- **Protocol 仅用于第三方形状接缝**——全项目唯一 Protocol 是适配器内的 `CcxtExchange`：它描述 raw ccxt 实例必须提供的最小表面，我们无法让 ccxt 继承我们的 ABC，测试 Fake 结构化满足。它标 `@runtime_checkable`，由一致性测试 `isinstance(ccxt.binance(), CcxtExchange)`（构造不触网）拦截"ccxt 改名/移除方法"的漂移。

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
| `smart_trader/exchange/ports.py` | 端口（ABC）+ `Exchange` 组合接口，零第三方依赖 |
| `smart_trader/exchange/binance/binance_exchange.py` | Binance 适配器，两层职责：`CcxtExchange` Protocol（ccxt 最小类型化接口，`@runtime_checkable`，入站载荷/出站参数用 TypedDict 词汇表声明消费面）+ builder 函数（`build_public_ccxt` / `build_private_ccxt`：构造选项/demo 开关/构造即 `load_markets`，`raw_factory` 注入缝供测试打桩）→ `BinanceExchange(Exchange)`（领域对象 ⇄ ccxt 翻译，只依赖注入的 `CcxtExchange`，双实例按能力分流）。组装入口 `create_binance_exchange`：公共实例（无密钥）承载行情，私有实例（`is_test=True` 时 demo）承载交易/账户。附带非端口能力 `cancel_open_orders`（demo 专用） |
| `smart_trader/storage/records.py` | 存储记录模型（Pydantic）：`GridPositionRow` / `OrderRecord`，存储层对外的数据契约 |
| `smart_trader/storage/schema.py` | 表结构元数据（SQLAlchemy Core）：schema 唯一来源（列/约束/索引），建库走 `metadata.create_all` |
| `smart_trader/storage/order_journal.py` | 订单日志仓储（SQLAlchemy Core 语句 + 事务）：`grid_positions`（逻辑仓位，含 `closed_reason` 归档原因；打开即校验结构，缺列时报错内联等价 ALTER 语句）+ `orders`（订单，`status` 为空承担 WAL） |
| `scripts/migrations/` | 库结构演化编号脚本（纯运维，与业务包解耦）：每次演化新增一个编号文件，手动对存量库执行一次；幂等，重复执行无害 |
| `smart_trader/execution/limit_order_chaser.py` | 追单执行策略 |
| `smart_trader/execution/journaled_trading.py` | 记账下单包装（TradingPort 装饰器）：预写仓位/订单 → 下单 → 回写结果 |
| `smart_trader/execution/order_reconciler.py` | 启动对账：扫 `status IS NULL` 的订单 → 查交易所 → 回写 |
| `smart_trader/strategy/` | 策略基类与网格策略。**四层职责边界**：`signal`（`Signal` ABC，只回答"行情现在是什么状态"）/ `entry_gate`（`EntryGate` 可组合入场闸门链）/ `exit_policy`（`ExitPolicy` 可组合离场策略链）/ `signal_grid_strategy`（**只做装配 + 仓位与订单管理**，不做任何入场/离场判定）。网格策略其余模块：`grid_position`（仓位对象 + `load_positions` 批量还原）/ `signal_grid_config`（配置 + `CloseOutcome`）/ `grid_order_gateway`（记账下单入口）/ `position_lifecycle`（订单结算：入场单成交结算[盘中] / 撤剩余入场单[出场流程] / 出场单跟踪[每根完成K线]）/ `position_reconciler`（持仓对账：每根完成K线以交易所实际持仓为准收敛本地缺口，外部平仓场景的归档与可平量修正）/ `alpha_trend_signal`（`AlphaTrendSignal` 实现，`alpha_trend_grids_signal.py` 已删除，语义由 `SignalKind.TRIGGER` 承载） |
| `smart_trader/data_event_loop.py` / `task/` | 数据事件循环与任务分发。两种数据源模式：`BinanceDataEventLoop`（WebSocket 推送，解析一次为 Kline 后分发）与 `TimerDataEventLoop`（按 timeframe 定时经 REST 拉取，见第九节）；事件载荷统一为 `Kline` 领域对象 |

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
| Step 10 | 逻辑仓位重构：`grid_positions` 只存参数与运行时状态，价格/数量/相位由 `orders` 派生；删 `order_ids`（WAL 改由 `orders.status` 为空承担）；入/出场按 `position_side` 归类；`current_stop_price` → `trailing_stop_price` | 已完成 |
| Step 11 | 删除 `GridPositionManager`：仓位登记表内联进策略（`positions` / `add_position` / `remove_positions`），批量还原下沉为 `grid_position.load_positions`；`load_positions(force)` 的 `force` 参数随之删除（唯一调用点在 `__init__`）；行为零变化 | 已完成 |
| Step 12 | 回调互斥重构：`on_kline_lock` + `on_kline_finished_lock` 合并为 `_callback_lock`；`_call_on_kline_finished` 阻塞等待（交易决策永不被跳过），`_call_on_kline` 非阻塞（盘中更新可跳过 + warning 日志）；`SignalGridStrategy._positions_lock` 随之删除（回调已串行化） | 已完成 |
| Step 13 | **信号抽象重构（职责边界）**：`Signal` 只输出行情状态（`MarketSignal` / `SignalDirection`），删 `side`/`run`/`is_entry`/`is_exit`/`reverse`；入场判定下沉为可组合 `EntryGate` 闸门链，离场判定下沉为可组合 `ExitPolicy` 策略链（`force` 表达"可覆盖休息中的出场挂单"）；`AlphaTrendGridsSignal` 删除，语义由 `SignalKind.TRIGGER` 承载；`SignalKind` 取代闸门的 bool flag；`PositionLimit` 成为仓位上限与"达到上限止损"的唯一权威来源；**删除 `position_reverse`**（方向直接来自 config，顺势加仓应写新信号 + 正常闸门）；**修掉 `close_position` 手动全平触发止损回调并暂停策略的缺陷**（拆 `stop_loss_reached` 与 `force_all` 两个独立触发源）；`AlphaTrendSignal` 改用 v2 指标的趋势累积计数，去掉 `first_run` 历史反推（该反推含当前 K 线，导致重启后漏信号）；`check_open_position` 由 98 行降到 5 行（只剩闸门调用 + 建仓），策略内不再出现任何入场/离场判定；新增 `ErrorCode.MARKET_DATA_UNAVAILABLE`（设计见 `.memory/03-coding-design.md` 第三节、模型见 `02-domain-model.md` 第八节） | 已完成 |
| Step 14 | 集成测试三层（行为端到端与账本不变量 / 进程内恢复 / SIGKILL 子进程档，设计见第八节） | 待实施 |
| Step 15 | 事件载荷统一为 `Kline`（解析点唯一）；新增 `TimerDataEventLoop` 定时拉取型事件循环（周期边界触发 `on_kline_finished` / 周期中点触发 `on_kline`，设计见第九节）；timeframe 换算收敛为 `model.timeframe_seconds` 单一权威；新增独立入口 `run_timer.py` | 已完成 |
| Step 16 | 适配器双 ccxt 实例按能力分流：`_public_exchange`（生产、**无密钥**）承载行情，`_private_exchange`（`is_test=True` 时 demo）承载交易/账户——与 WS 模式"生产行情流 + demo 交易"语义对齐，行情路径零凭据 | 已完成 |
| Step 17 | **修复 `fetch_ohlcv` 位置传参缺陷**：适配器把 `limit` 传入 ccxt `fetch_ohlcv(symbol, timeframe, since, limit)` 的 `since` 槽（`startTime≈1970`），恒拉数据集最老 500 根K线——定时模式的"陈旧K线"告警与 **WS 模式的历史初始化（300 根"历史"实为 2020 年数据）** 皆源于此；`CcxtExchange` Protocol 与 `FakeCcxtExchange` 的 `fetch_ohlcv` 签名随之对齐 ccxt 原生（否则错位会被类型检查与测试双重掩盖），并补回归测试。此前记录的"demo 行情镜像不稳定"结论有误，实为此缺陷 | 已完成 |
| Step 18 | **`CcxtExchange` 契约双侧静态校验 + 组装缝**：此前 Protocol 只校验测试侧 Fake，真实 ccxt 侧经 `cast`/`Any` 赋值盲信（声明了 ccxt 上不存在的方法只有运行时才暴露）。新增 `CcxtBinanceExchange` 显式继承 Protocol（pyright 从此校验真实实现侧），入站载荷与出站参数引入 TypedDict 词汇表（`CcxtOrder` / `CcxtPosition` / `CcxtOrderParams` 等，键按实际消费方式声明必填性，`CcxtOrder` 保持 total=False 以允许注入残缺响应），适配器内 4 处 `cast` 清零；`BinanceSwapClient` 改为纯依赖注入（删除 `__new__` 注入 hack 与死代码 `create_ccxt_binance`），组装入口 `create_binance_swap_client`；修复 7cc0f9d 遗留红测试（测试引用从未实现的 `create_exchange` 参数），构造期契约测试改经 wrapper 的 `raw_factory` 注入打桩；顺手修复存量 `data_event_loop.py` 的 pyright strict 报错（恢复被误删的 `cast` 用法，运行时零变化）。后续收敛：Protocol 成员 `fapiPublicGetExchangeInfo` 更名 `exchange_info`（seam 只表达需求，币安端点名归 wrapper 委托实现），client 交易规则缓存领域化为 parsed `SymbolInfo`（按 `symbol.binance()` 键，raw dict 不再滞留 client 状态） | 已完成 |
| Step 19 | **client 包整体迁入 exchange 包 + 端口 ABC 化（两层适配器）**：`client/ports.py` → `exchange/ports.py`、`client/binance_client.py` → `exchange/binance/binance_exchange.py`，`client/` 包删除。三端口由 `typing.Protocol` 改为 `abc.ABC` + `@abstractmethod`，新增 `Exchange` 组合接口（装配层持有完整能力）；`BinanceExchange` 与测试 Fake（`FakeTradingPort` / `FakeMarketDataPort` / `StatefulFakeExchange` / `JournaledTradingPort`）显式继承端口。Protocol 收敛为唯一第三方接缝 `CcxtExchange`（标 `@runtime_checkable`，新增真实 ccxt 实例一致性测试）。**删除纯委托层 `CcxtBinanceExchange`**：构造逻辑（public/private/demo/load_markets）收进 builder 函数 `build_public_ccxt` / `build_private_ccxt`（`raw_factory` 注入缝保留），适配器从三层收敛为两层——`BinanceExchange` 对 `CcxtExchange` 类型双实例的每个调用点从此直接受 Protocol 静态校验（Step 17 的 limit/since 槽位错位正属此类错误）；接缝成员 `exchange_info` 随之回归 ccxt 方言名 `fapiPublicGetExchangeInfo`（Step 18 的"seam 只表达需求"改名依附于 wrapper 委托层，wrapper 删除后接缝必须描述 raw 实例的真实表面，一致性测试才能拦截成员漂移）。命名对齐：`BinanceSwapClient`→`BinanceExchange`、`create_binance_swap_client`→`create_binance_exchange`、`bootstrap.create_binance_client`→`create_binance_exchange`；`cancel_open_orders` 保留为非端口能力（demo 专用）。类型准则（内部 ABC / Protocol 仅限第三方接缝）见第二节 | 已完成 |
| Step 20 | **顺势逐层加仓 + 逆势逐层减仓**：新增 `TrendSpacingGate`（顺势阶梯闸门：以最外层仓位为基准，盈利方向走出间距才加仓；与 `GridSpacingGate` 互斥装配，由 `trend_spacing_rate` 选择，见 `02-domain-model.md` 八.3.2）与 `LayeredReducePolicy`（信号反向命中时每根完成 K 线只减最外层一层、亏损也减、`force=True` 撤单重挂保证实际减仓；与 `SignalExitPolicy` 二选一，`enable_exit_signal` 仍为总开关）；`ExitContext` 增加 `positions` 字段（与 `EntryContext` 对称，纯数据）；config 新增 `trend_spacing_rate` / `enable_layered_reduce`（设计见 `03-coding-design.md` 三.10/三.11） | 已完成 |
| Step 21 | **修复入场单成交永不结算的职责耦合缺陷**：`_on_kline` 原以 `resting_take_profit is None → return` 整体短路，未开启限价止盈的配置下入场单成交永远无人结算（`position.quantity` 恒 0、账本停 `open`、止盈/止损/间距全失真；实测事故见 `03-coding-design.md` 三.12）。拆分：`PositionLifecycle.settle_entry_fills`（盘中每 tick 无条件只结算不撤单，None 时按同 `OrderReconciler` 的 5s 最小年龄口径防 settle 延迟）与 `settle_pending_entries`（撤剩余入场单 + 结算，仅出场流程调用）；`_on_kline` 休息止盈块独立守卫 | 已完成 |
| Step 22 | **修复固定止损费率开仓后冻结的缺陷（实测事故）**：原实现开仓时把 `stop_loss_rate` 一次性烘焙进 `trailing_stop_price`，`StopLossPolicy` 也只读该字段 —— 之后修改 config 的 `order_stop_loss_rate` 或库里的 `stop_loss_rate` 对存量仓位永远无效（字段看着是活参数，实际是死快照）。拆分语义：**固定止损**按 `仓位均价 × (1 ∓ stop_loss_rate)` 实时判定（活参数，`_open_position` 不再为纯固定止损写快照字段）；**跟踪止损**独占 `trailing_stop_price`（ratchet 字段，播种口径不变）；`enable_stop_loss` 仍是总开关，`enable_trailing_stop` 只选模式（设计见 `02-domain-model.md` 八.5、`03-coding-design.md` 三.13） | 已完成 |
| Step 23 | **信号离场拆分为两个独立开关**：`enable_exit_signal`（总开关）+ `enable_layered_reduce`（口味）删除，改为 `enable_signal_take_profit`（信号止盈, 默认 True → `SignalExitPolicy`）与 `enable_signal_stop_loss`（信号止损, 默认 False → `LayeredReducePolicy`）；二者独立装配可并存 —— 最外层由止损 force 剥（亏损也减），内层盈利层由止盈离场，同层命中走 force 合并；config 加 `extra="forbid"` 拦截拼写漂移（设计见 `03-coding-design.md` 三.11） | 已完成 |
| Step 24 | **信号离场按盈亏状态分派**：反向信号命中时同一层按盈亏走且仅走一个分支 —— 浮盈（`ratio > 0`）+ `enable_signal_take_profit` → 止盈（非 force）；浮亏（`ratio < 0`）+ `enable_signal_stop_loss` → 止损（force，仅最外层逐层）；持平不触发。`LayeredReducePolicy` 从"不要求盈利"收紧为"必须浮亏"，`SignalExitPolicy` 从"盈利 ≥ min_rate"改为"浮盈即止盈"，`exit_signal_take_profit_min_rate` 删除（防 死参数 教训重演）（设计见 `03-coding-design.md` 三.11） | 已完成 |
| Step 25 | **`ExitContext.close` 必填 + `_exit_context` 无行情契约**：删除 `close=0.0` 静默兜底（0.0 会变成止损/止盈判定的"真实价格"），`kline is None` 抛 `MARKET_DATA_UNAVAILABLE`，与入场侧 `EntryContext.require_close` 对称（设计见 `03-coding-design.md` 三.14） | 已完成 |
| Step 26 | **交易所响应解析"缺失 ≠ 零值"**：`_to_float` 拆分"缺失(None)"与"0"；`amount` 缺失抛 `ORDER_RESPONSE_INVALID`、`filled` 缺失归 0、`price/average` 的 0 与缺失都归 None；`Position.entry_price` 必填；`JournaledTradingPort` 的 `or ""` 空串仓位号缺陷修复（穿透 `in_` 过滤导致成交事实静默丢失）+ 数据校验弃用 `assert`（`python -O` 剥离）统一改 `if...raise SmartTraderError`（设计见 `03-coding-design.md` 三.15） | 已完成 |
| Step 27 | **持仓对账（外部平仓同步）**：新增 `PositionReconciler`（`strategy/position_reconciler.py`），每根完成 K 线经 `AccountPort.positions` 查询交易所实际持仓并与本地派生量比对：本地多出（外部手动平仓）按 LIFO（最外层优先，与 `LayeredReducePolicy` 同序）分摊缺口 —— 被全吃的仓位先撤挂单结算再内部归档（不下对冲单），部分被吃的仓位记录外部扣减量供平仓下单 clamp；交易所多于本地（来源不明仓位）仅告警不自动改动；查询/撤单异常单仓容忍，下一根重试。策略构造新增必需依赖 `account: AccountPort`；启动时在 `load_positions` 之后立即对账一次（设计见 `03-coding-design.md` 三.16，不变量见第十节 12） | 已完成 |
| Step 28 | **归档原因落库（审计可查）**：`grid_positions` 新增 `closed_reason` 列（`PositionCloseReason` 枚举：`EXIT_SETTLED` / `EXTERNAL_RECONCILED` / `ORPHAN_REAPED` / `ENTRY_DISCARDED`），与 `active=0` 同语句写入。**结构演化与业务包完全解耦**：schema.py 是"当前结构"的唯一声明，代码不维护迁移历史；演化以编号脚本归档于 `scripts/migrations/`（`001_add_grid_positions_closed_reason.py`，幂等，手动对每个存量库执行一次），`OrderJournal` 打开缺列旧库时从 schema 元数据动态生成等价 ALTER 语句并响亮报错（有专门测试锁定不自动改表）。`_archive` 按归档来源分组传 reason，`CloseOutcome.archived` 拆为 `exit_settled` / `entry_discarded` 两个具名列表 —— 顺带修复"出场结算耗尽的仓位在 `check_close_position` 被误判为 `entry_discarded` 覆盖 `EXIT_SETTLED`"的重复归档缺陷（归档判定收进"确有入场单被撤"分支）。**审计列必须排在表尾**：`ALTER ADD COLUMN` 只能追加，schema/冻结契约中 `closed_reason` 置于 `updated_at` 之后才能保证手动演化后结构与契约一致（设计见 `02-domain-model.md` 七、`03-coding-design.md` 三.16） | 已完成 |

### 待办清单（Step 5）

按优先级：

1. ~~**网格策略关键分支缺测试**~~ —— **已全部覆盖**：
   `enable_max_order_stop_loss` + `paused_after_stop_loss` 见
   `tests/test_signal_grid_strategy.py`（达到上限全平并暂停 / 不暂停仍不开新仓）与
   `tests/test_stop_loss.py`（暂停后仍无条件跟踪出场单）；
   `per_order_qty == 0` 见 `tests/test_signal_grid_strategy.py`（零数量只登记空仓位不发单、
   账本有仓位行无订单行、间距闸门照常生效）；
   `grid_spacing_rate` 加仓间隔见闸门级 `tests/test_entry_gate.py`（正负间距语义、
   含等号边界、极值基准）与策略级 `tests/test_signal_grid_strategy.py`（间距内拦截 /
   间距外加仓 / 负间距顺势加仓端到端）；
   `profit_level` / `profit_and_loss_ratio` 分档边界由消费方锁定
   （`tests/test_exit_policy.py` 的 exactly_at 系列、`tests/test_signal_grid_rules.py` 空头分档）；
   `close_position` 手动全平见 `tests/test_signal_grid_signal_path.py`；
   `position_reverse` 随该字段删除作废（Step 13）。
2. ~~**信号层缺单元测试**~~ —— **已由 Step 13 完成**：`Signal.is_entry` / `is_exit` 与
   `AlphaTrendGridsSignal` 已随职责重构删除；替代覆盖见 `tests/test_market_signal.py`、
   `tests/test_entry_gate.py`、`tests/test_exit_policy.py`、`tests/test_alpha_trend_signal_unit.py`。
3. ~~**`AlphaTrendGridsSignal` 死代码**~~ —— **已由 Step 13 解决**：整个文件随类一并删除。
4. ~~**`.memory/03-coding-design.md` 内容与代码脱节**~~ —— **已由 Step 13 一并修正**：
   `records.py` 的 `IdRecord` / `OrderIntent`、`_migrate_legacy_schema`、
   `confirm_entry` / `confirm_exit`、`order_ids.intent` 失效引用已清除，并新增第三节
   「信号与规则层设计」覆盖 strategy 层（此前只覆盖 storage 层）。
5. **日志与配置模块无测试**（`log.init_logging` 的级别解析、`config` 的必填校验）。
6. ~~**顺势加仓**~~ —— **已由 Step 20 解决**：需要"价格顺趋势上涨/下跌到一定距离即加仓"时，
   用 `TrendSpacingGate`（`trend_spacing_rate` 装配后替换 `GridSpacingGate`，见
   `02-domain-model.md` 八.3.2）；"逆势逐层减仓"由 `enable_signal_stop_loss=True`
   装配 `LayeredReducePolicy` 承担（Step 23 拆分后与 `enable_signal_take_profit`
   独立）。
   `Step 13` 已删除 `position_reverse` 这个反向开关，方向直接由 config 声明。

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
- **手动全平触发止损回调并暂停策略**（Step 13 修复）：原实现把
  `达到上限止损 or self.close_position` 合并成一个 `stop_loss_order_all`，
  于是用户主动的手动全平（`close_position = True`）也会调用 `on_stop_loss_order_all()`
  并在 `paused_after_stop_loss` 下把 `is_running` 置 `False` —— 手动动作被当成止损。
  修复：拆成两个独立触发源，`stop_loss_reached` 只管回调与暂停，
  `force_all`（`stop_loss_reached or close_position`）只管离场判定。离场行为不变。
- **入场单成交永不结算（Step 21 修复，实测事故）**：`_on_kline` 把"入场单结算"
  与"休息止盈单挂单"用同一守卫短路（`resting_take_profit is None → return`），
  未开启限价止盈的配置下入场单成交后 `position.quantity` 恒为 0、账本行停在
  `open`，止盈/止损/间距加仓全部失真，直至信号反转才可能被 `settle_pending_entries`
  顺带纠正；孤儿回收也因内存行仍是 `open` 而永不触发。实测：入场单 14:38 成交、
  进程存活且正常开第二仓，账本直至进程死亡仍为 `open`。
  修复：`PositionLifecycle.settle_entry_fills`（盘中只结算不撤单）+
  `_on_kline` 解耦守卫（见不变量 11 与 `03-coding-design.md` 三.12）。
  旧盘中路径在限价止盈开启时还会复用 `settle_pending_entries` 把刚挂的入场单
  一秒内撤掉（推翻追单成果）——撤单语义随本次拆分收敛到出场流程。
- **固定止损费率开仓后冻结（Step 22 修复，实测事故）**：开仓时把 `stop_loss_rate`
  一次性烘焙进 `trailing_stop_price`，`StopLossPolicy` 也只读该字段 —— 字段看着是
  活参数，实际是死快照：修改 config 的 `order_stop_loss_rate` 或库里的
  `stop_loss_rate` 对存量仓位永远无效。实测：用户把费率 0.05 改成 0.005 并重启，
  触发价仍停在开仓时的 0.0834（= 开仓价 × 0.95），0.5% 止损始终不触发。
  修复：固定止损按 `仓位均价 × (1 ∓ stop_loss_rate)` 实时判定（活参数，重启即生效）；
  跟踪止损独占 `trailing_stop_price` ratchet 字段（播种口径不变）；
  `_open_position` 不再为纯固定止损写快照（见 `02-domain-model.md` 八.5、
  `03-coding-design.md` 三.13）。
- **指标实现污染共享 klines DataFrame（Step 23 修复）**：`alpha_trend_indicator`
  docstring 声称"返回原 DataFrame 的副本"，实现却把指标列直接写进入参 ——
  `is_breakout` / `long_entry` / `short_exit` 等是 **bool 列**，策略共享的
  klines DataFrame 被加列后，`_update_klines` 的整行赋值
  （`df.loc[idx] = kline_dict`，只含 7 个 K 线字段）对这些列 NaN 补全，
  向 bool 列写 nan 触发 pandas FutureWarning
  （"Value 'nan' has dtype incompatible with bool"，pandas 3.0 起将直接报错）。
  修复：入参 `df.copy()`（与 docstring 对齐），Signal 契约补全
  "不得替换、清空或增删列"；回归测试
  `test_indicator_does_not_mutate_input_dataframe` 锁定。
- **外部平仓后策略按本地虚高数量平仓，陷入拒单死循环（Step 27 修复）**：
  本地派生量只认订单流水，用户在交易所手动平仓后本地不知道 —— 平仓单被
  交易所整单拒绝（对冲模式 -2022），`Task.safe_run` 吞异常、下一根 K 线原样
  重试，仓位永远平不掉、偏差永不收敛。修复：新增 `PositionReconciler`
  持仓对账（每根完成 K 线 + 启动时，交易所实际持仓为唯一收敛权威，
  设计见 `03-coding-design.md` 三.16、不变量见第十节 12）。
- **`Signal` 不是真 ABC**（Step 13 修复）：`@abstractmethod` 修饰 `run()` 但类未继承 `ABC`，
  抽象方法完全失效，任何子类都可实例化并返回 `None`。
- **`AlphaTrendSignal.first_run` 反推吞掉本次突破**（Step 13 修复）：反推取
  `df[_signal].dropna().iloc[-1]`，而那条记录**就是正在处理的当前 K 线**，
  于是 `current_signal` 被设成当前方向，随后的同向去重把本次突破吞掉 ——
  实测进程重启后首次求值恒为 `neutral`，必丢一个突破信号。
  修复：改用 `alpha_trend_indicator_v2` 的趋势累积计数口径，信号完全由 DataFrame
  决定，`first_run` 反推整体删除。
- **`evaluate()` 对空 DataFrame 抛 `IndexError`**（Step 13 修复）：
  `klines[_datetime].iloc[-1]` 在空表上越界。旧测试用 `try/except` + `assert ... in str(e)`
  把它盖住了。修复：入口加 `if klines.empty: return MarketSignal()`，
  并新增 `ErrorCode.MARKET_DATA_UNAVAILABLE` 供 `EntryContext.require_close()` 使用。
- **`max_order=1` 且开启上限止损时策略空转**：原判定式 `max_order - count <= 1`
  在空仓（count=0）时也为真，入场闸门永不开第一单；且若只放开空仓档，
  持有唯一仓位时 `1 - 1 <= 1` 仍触发强平，会变成"开了就平"的抖动。
  修复：`stop_loss_triggered` 在 `max_order <= 1` 时恒为 `False`——容量为 1
  不存在"最后一格"状态，上限止损整体不适用，单仓位保护由
  `enable_order_stop_loss`（价格维度止损）承担；策略构造时对该组合记录 warning
  （见 `02-domain-model.md` 第三节）。

## 八、测试策略

- 内存实现优先：`tests/fakes.py` 提供 `FakeCcxtExchange`（显式继承 `CcxtExchange` Protocol，pyright 校验 Fake 侧签名）/ `FakeTradingPort` / `FakeMarketDataPort`（显式继承端口 ABC）/ `FakeSignal` / `RecordingGate`，以及 `make_position_row` / `make_order_record` / `make_market_signal` 等构造工厂，默认测试不触网（集成测试标记 `@pytest.mark.integration`）。
- 端口契约测试通过假端口验证业务行为，适配器测试通过假 ccxt 验证翻译规则；ccxt 实例构造契约（凭据/demo 只进私有实例、构造即预载市场）经 builder 函数的 `raw_factory` 打桩锁定；`fetch_ohlcv` 的 limit 必须关键字传参的回归锁，落在 `BinanceExchange` 对 `FakeCcxtExchange` 的调用断言上；`CcxtExchange` 接缝由真实 ccxt 实例一致性测试（`isinstance`，构造不触网）再锁一道。
- 信号与规则层分层测试（Step 13）：值对象（`test_market_signal.py`）→ 闸门（`test_entry_gate.py`）
  → 离场策略与止盈/止损分档（`test_exit_policy.py`）→ 信号实现（`test_alpha_trend_signal_unit.py`）
  → 仓位对象与还原（`test_grid_position.py`）→ 编排（`test_signal_grid_strategy.py`）
  → 装配契约（`test_signal_grid_rules.py`），底层先测。
- sqlite 日志测试统一使用 `:memory:` 数据库（StaticPool 共享单连接），不落盘、不共享状态。
- 库结构由 `tests/test_storage_schema.py` 以冻结的旧版 DDL 快照做等价性校验：metadata 建出的库与历史契约（列/约束/默认值/索引）不一致即失败。
- **集成层（Step 14）**：真实装配链（策略 + Gateway + `JournaledTradingPort` + `OrderReconciler`）
  对接**真实 sqlite 文件账本**（tmp_path，WAL 生效）与**状态化交易所** `tests/fake_exchange.py`
  （订单簿 + 成交脚本 + one-shot 故障注入 `lose_response_next` / `drop_request_next` + 可选 JSON 持久化），
  由 `tests/integration_harness.py` 装配（`KlineDriver` 走 `run()` 公开入口；`restart` 走真实重启路径
  = 构造函数内对账 + `load_positions`；`assert_ledger_matches_exchange` 做账本 ⇄ 交易所双向对账）。
  三个观察维度对应三个文件：行为端到端与账本不变量（`test_signal_grid_integration.py`）、
  进程内恢复（`test_signal_grid_recovery.py`，崩溃点逐个注入）、SIGKILL 子进程档
  （`test_crash_subprocess.py`，标 `@pytest.mark.integration` 默认排除，`uv run pytest -m integration` 运行）。
  对账断言的"已观察"语义：策略对成交是惰性感知（只在离场决策/盘中路径/出场跟踪时 query），
  账本保存最近一次查询时的事实 —— 委托事实（side/quantity/price）始终比对，
  执行事实（status/filled/avg_price）仅在该单已被策略查询过时比对（簿条目 `observed` 标记）。

## 九、事件循环与任务

- `Task` 必须提供 `name`（用于日志定位），并统一经 `Task.safe_run` 调度：
  任务异常在此被捕获并记录，不会中断事件循环，也不会被线程池静默吞掉。
- **事件载荷统一为 `Kline` 领域对象**（Step 15）：`Task.run(kline: Kline)` /
  `DataEventLoop.loop(kline: Kline)`。ws 原始字符串只在 `BinanceDataEventLoop.on_message`
  解析一次，N 个任务不再各自重复 `json.loads`。
- 解析是纯函数（`parse_kline_message`，定义于 `data_event_loop.py`，属传输细节），
  无法解析时返回 `None` 并记 debug 日志，不允许向上抛异常。
- `StrategyTask` 按 `symbol + timeframe` 过滤 K 线，非本任务的直接忽略。

### 定时拉取型事件循环（`TimerDataEventLoop`，Step 15）

与 WebSocket 推送并列的第二种数据源模式：不建 ws 连接，由本地时钟按 timeframe 节奏
经 `MarketDataPort.fetch_ohlcv` 拉取 K 线驱动策略：

| 调度点 | 时机 | 拉取 | 触发 |
| --- | --- | --- | --- |
| 周期完成 | 每个 timeframe 边界 + `fetch_delay` | `limit=2` 取最后一根 `finished=True`（即"最后一根已完成K线"） | `on_kline_finished` |
| 周期中点 | timeframe 的 1/2 处 | `limit=1` 取当前进行中 K 线 | `on_kline` |

- 两者都经 `strategy.run(kline)` 分发，保留 DataFrame 维护、历史初始化与回调互斥语义。
- 错过的调度点自然跳过（只调度未来事件）；多 timeframe 合并调度取最早到期事件，
  **并列同刻的事件一齐分发**（如 1m 与 5m 边界重合）。
- 拉取失败或尚无已完成K线（本地时钟落后）→ 记日志、本周期不分发，**不等待重试**，
  由下一个边界自然补上；**同一根K线（按 symbol+timeframe+时间戳）不重复分发**
  ——时钟漂移时防止重复触发 `on_kline_finished`。
- **陈旧K线拒绝分发**：落后预期边界超过一个周期 → 判定数据源异常，记 error 并拒绝，
  不写入去重记录（恢复后正常K线仍可分发）；落后 ≤1 个周期或超前 → 记"时钟漂移"
  警告后照常分发。中点拉到已收盘K线同样拒绝并记录实际时间戳。实例：Step 17 之前
  适配器把 `limit` 位置传参进 ccxt `fetch_ohlcv` 的 `since` 槽（`startTime≈1970`），
  恒拉数据集最老 500 根K线（2020 年），该规则在首个边界就拒绝了它们。
- `on_kline` 节奏与 WS 模式不同：定时模式每周期一次（中点），WS 模式随推送多次。
- 独立入口 `run_timer.py`（与 `run.py` 的 WS 装配互为替代，凭据装配共享 `bootstrap.py`）。

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
8. **信号只输出行情状态，不得携带持仓语义**（Step 13）：
   `Signal.evaluate` 只返回 `MarketSignal(direction, datetime)`，不含 `side`。
   方向消费方是 `EntryGate` / `ExitPolicy`，**策略内不得出现方向比较**。
9. **已有未成交出场单时，只有 `force=True` 的离场决策可覆盖**（Step 13）：
   `force=True` 仅由 `ForceExitPolicy`（仓位外部强制）与 `StopLossPolicy`（仓位自身止损）产出；
   止盈类策略恒为 `force=False`，保留原挂单不重复下单。
   `ExitPolicyChain` 的合并规则（`should_exit = any`、`force = any`）**与顺序无关**。
10. **`PositionLimit` 是仓位数量上限与"达到上限止损"的唯一权威来源**（Step 13）：
    开仓闸门与离场强制判定必须读同一对象，杜绝两处各写一遍导致的漂移。
11. **入场单成交由盘中路径每 tick 无条件结算**（Step 21）：
    每根盘中 tick 对 `pending_entry_orders` 逐笔查询交易所并回写事实
    （`settle_entry_fills`，只 settle 不撤单）；"撤销未成交入场单"
    （防继续建仓）只属于出场流程（`settle_pending_entries`，由
    `check_close_position` 触发）。盘中路径不得因 `RestingTakeProfitPolicy`
    未装配而跳过结算 —— 入场单结算是记账职责，与限价止盈开关无关。
12. **持仓对账以交易所实际持仓为唯一收敛权威**（Step 27）：
    每根完成 K 线（含启动后首根前）经 `AccountPort.positions` 查询该 symbol
    `position_side` 方向的实际持仓量，与本地派生总量比对：
    - **本地多出（外部手动平仓）**：缺口按 LIFO（最外层优先，与
      `LayeredReducePolicy` 同序）分摊 —— 被全吃的仓位**先撤挂单结算事实
      再内部归档**（绝不下对冲单）；部分被吃的仓位记录外部扣减量，
      平仓/休息止盈下单量一律 clamp 为 `quantity - 扣减量`；
    - **交易所多于本地**（手动加仓 / 来源不明仓位）：仅告警，**不自动收编**；
    - **数量一致**：无动作；
    - 对账结果**不回写账本数字**：`orders` 行只保存订单事实，外部平仓的
      成交价本地拿不到，不伪装记账；归档仓位在账本中保留原始订单行 + 日志留痕；
    - 查询异常跳过本轮、撤单异常单仓容忍，均下一根重试，绝不中断主循环。

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
