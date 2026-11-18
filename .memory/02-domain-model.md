# 领域模型

## 一、核心对象

| 对象 | 位置 | 说明 |
| --- | --- | --- |
| `Symbol` | `model.py` | 交易对。`base`/`quote` 只是数据，格式转换由 `ccxt()` / `binance()` / `from_ccxt()` 提供 |
| `SymbolInfo` | `model.py` | 交易所交易规则：tick/step/最小最大价量/最小名义价值；负责价格数量格式化与校验 |
| `Kline` | `model.py` | K 线，`finished` 表示该根是否已收盘 |
| `OrderRequest` | `model.py` | 下单请求（领域对象） |
| `OrderResult` | `model.py` | 订单结果（领域对象），`place`/`query`/`cancel` 的统一返回 |
| `Position` | `model.py` | 持仓（`entry_price` **必填**，无默认值 —— 0.0 是"看起来合法实则错误"的成本价） |
| `GridPosition` | `strategy/grid_position.py` | **逻辑仓位**：一个格子的完整生命周期；只持策略参数与运行时跟踪状态，价格/数量/相位全部由订单派生 |
| `GridPositionRow` | `storage/records.py` | `grid_positions` 表的一行：`GridPosition` 的持久化形态，不含策略行为 |
| `OrderRecord` | `storage/records.py` | `orders` 表的一行：一次委托（`status` 为空表示结果未确认，承担 WAL 标记） |
| `MarketSignal` | `model.py` | 行情信号快照：只描述"现在是什么状态"，不含持仓语义，不入库 |
| `PositionLimit` | `model.py` | 仓位数量上限：开仓上限闸门与"达到上限全部止损"的唯一权威来源 |
| `Signal` | `strategy/signal.py` | 行情信号分析器（ABC）：无持仓语义、无入场/离场概念 |
| `EntryContext` | `strategy/entry_gate.py` | 入场闸门上下文（纯数据）：仓位列表 + 最新收盘价 + 行情信号 |
| `EntryGate` | `strategy/entry_gate.py` | 入场闸门（ABC）+ `EntryGateChain` |
| `ExitContext` | `strategy/exit_policy.py` | 离场策略上下文（纯数据）：最新收盘价（**必填**，无默认值）+ 行情信号 + 仓位列表 |
| `ExitDecision` | `strategy/exit_policy.py` | 离场决策：`should_exit` + `force`（force 可覆盖休息中的出场挂单） |
| `ExitPolicy` | `strategy/exit_policy.py` | 离场策略（ABC）+ `ExitPolicyChain` |

`GridPosition` 的**数量、均价、相位都不是存储字段**，而是由该仓位关联的 `orders` 派生
（定义见第七节）。入场/出场按 `position_side` 归类：`long` 下 buy=入场、sell=出场，
`short` 反之；`main_order_side` 只表示网格主攻方向，**不参与**入/出场归类。
模型配置 `extra="forbid"`，字段拼写错误会立即报错而不是被静默忽略。

### timeframe 表示

timeframe 是普通字符串（如 `"1m"` / `"1h"` / `"1d"`），不设枚举（ccxt 与币安侧均以字符串流转）。
**秒数换算的权威来源是 `model.timeframe_seconds(timeframe) -> int`**：支持 s/m/h/d/w，
非法输入抛 `ValueError`。适配器内的毫秒换算（`binance_client._parse_timeframe_ms`）由它派生
（×1000，并把 `ValueError` 翻译为 `SmartTraderError(EXCHANGE_ERROR)`，保持适配器错误契约）。
禁止再出现第三份换算实现。

## 二、枚举（统一定义于 `model.py`，禁止硬编码字符串）

| 枚举 | 取值 |
| --- | --- |
| `OrderSide` | `BUY` / `SELL` |
| `PositionSide` | `LONG` / `SHORT` |
| `OrderType` | `LIMIT` / `MARKET` |
| `TimeInForce` | `GTC` / `IOC` / `FOK` / `GTX`(post only) |
| `OrderStatus` | `OPEN` / `CLOSED` / `CANCELED` / `REJECTED` / `EXPIRED` |
| `PlaceOrderBehavior` | `NORMAL` / `CHASER`(追单并等成交) / `CHASER_OPEN`(追单只挂单) |
| `SignalDirection` | `LONG` / `SHORT` / `NEUTRAL`（行情方向，纯行情事实） |
| `SignalKind` | `TRIGGER`（无方向倾向）/ `DIRECTIONAL`（有方向倾向） |
| `PositionCloseReason` | `EXIT_SETTLED`（出场成交正常归档）/ `EXTERNAL_RECONCILED`（对账发现交易所已无对应持仓，内部关闭）/ `ORPHAN_REAPED`（空仓位超时回收）/ `ENTRY_DISCARDED`（未成交入场单丢弃后归档） |
| `ErrorCode` | `exceptions.py`，见第五节 |

## 三、订单字段

### OrderRequest

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `custom_id` | `str = ""` | 客户端订单号（幂等标识）。默认空串，由 `JournaledTradingPort.place()` 分配合法值后覆盖；适配器收到空串时抛 `SmartTraderError` |
| `symbol` | `Symbol` | |
| `side` | `OrderSide` | |
| `type` | `OrderType` | |
| `quantity` | `float` | `> 0` |
| `price` | `float \| None` | 限价单必填且 `> 0`；市价单必须为空 |
| `position_side` | `PositionSide` | 双向持仓模式必填 |
| `time_in_force` | `TimeInForce \| None` | 限价单可空（交易所默认 GTC）；追单第一档价格取 `price` |

### OrderResult

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `custom_id` | `str` | 追单可能改写，以交易所返回为准 |
| `symbol` / `side` | `Symbol` / `OrderSide` | |
| `status` | `OrderStatus` | |
| `quantity` | `float` | 委托数量 |
| `filled_qty` | `float` | 已成交数量（**部分成交的唯一事实来源**） |
| `price` | `float \| None` | 委托价；市价单为空 |
| `avg_price` | `float \| None` | 成交均价 |

### Position

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `symbol` | `Symbol` | ccxt 的 `DOGE/USDT:USDT` 解析为 base/quote |
| `side` | `PositionSide` | 交易所 `long`/`short`，其余视为异常 |
| `quantity` | `float` | 持仓数量，平仓后为 0 |
| `entry_price` | `float` | 开仓均价，缺失时为 0 |

### MarketSignal

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `direction` | `SignalDirection` | 行情方向，默认 `NEUTRAL` |
| `datetime` | `str` | 触发该信号的 K 线时间；空串表示尚无触发 |

**不入库**（本轮明确不引入 `signals` 表）。模型 `extra="forbid"`。

### PositionLimit

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_order` | `int` | 最大逻辑仓位数 |
| `enable_stop_loss` | `bool` | 达到上限时是否全部止损 |

- `reached(count)` = `count >= max_order`：仓位数已达上限，禁止再开仓。
- `stop_loss_triggered(count)` = `enable_stop_loss and max_order - count <= 1`：只剩 ≤1 个空位时触发全部止损。

> **`max_order <= 1` 时上限止损不适用**：容量为 1 时空仓（0）与满仓（1）相邻，不存在
> "最后一格"状态——空仓触发会永不开第一单，满仓触发会开了就强平（开平抖动）。
> 因此 `stop_loss_triggered` 在 `max_order <= 1` 时恒为 `False`，策略正常交易唯一仓位，
> 仓位保护由单笔止损（`enable_order_stop_loss`，价格维度）承担；策略构造时对该组合
> 记录 warning，避免静默失效。

## 四、ccxt 翻译表（只在适配器内使用）

| 领域 | ccxt 统一字段 | 备注 |
| --- | --- | --- |
| `OrderRequest.custom_id` | `params.newClientOrderId` | |
| `OrderRequest.position_side` | `params.positionSide` | 双向持仓模式 |
| `OrderRequest.time_in_force` | `params.timeInForce` | `GTX` 即 post only |
| `OrderResult.status` | `order.status` | `open`/`closed`/`canceled`/`rejected`/`expired`；其余（如 `canceling`）→ `ORDER_RESPONSE_INVALID` |
| `OrderResult.side` | `order.side` | 交易所必回传；缺失或无法解析 → `ORDER_RESPONSE_INVALID`（已实测 place/query/cancel 六种场景均回传） |
| `OrderResult.filled_qty` | `order.filled` | 由交易所 `executedQty` 归一化 |
| `OrderResult.price` | `order.price` | 市价单为 `0`/空 → `None` |
| `OrderResult.avg_price` | `order.average` | 由 `avgPrice` 归一化 |
| `Position.*` | `fetch_positions()` | `contracts` / `entryPrice` |

## 五、错误码

| ErrorCode | 触发场景 | 可重试 |
| --- | --- | --- |
| `ORDER_NOTIONAL_TOO_SMALL` | 名义价值低于交易所最小值 | 否 |
| `ORDER_QUANTITY_TOO_SMALL` | 数量低于交易所最小值 | 否 |
| `ORDER_WOULD_TAKE_LIQUIDITY` | post only 单会立即成交被拒（-5022） | 换价重试（`is_transient = False`） |
| `ORDER_REJECTED` | 交易所确定性拒绝（精度、参数、状态等） | 否 |
| `ORDER_NOT_FOUND` | 订单不存在（-2011） | 否（`query`/`cancel` 转为返回 `None`） |
| `ORDER_NOT_PLACED` | 追单递进耗尽仍未挂上 | 由调用方降级处理 |
| `INSUFFICIENT_FUNDS` | 保证金/余额不足 | 否 |
| `EXCHANGE_RATE_LIMITED` | 触发限频 | 是（`is_transient = True`） |
| `EXCHANGE_UNAVAILABLE` | 网络/超时/交易所不可用 | 是（`is_transient = True`） |
| `EXCHANGE_ERROR` | 其它交易所错误 | 视情况 |
| `ORDER_RESPONSE_INVALID` | 交易所响应缺少必需字段或无法解析 | 否 |
| `SYMBOL_INFO_UNAVAILABLE` | 交易规则缺失（如缺少过滤器） | 否 |
| `MARKET_DATA_UNAVAILABLE` | 尚无行情（缺最新 K 线/收盘价） | 否 |

## 六、持久化

订单持久化已从 JSON 文件（`OrderRecorder` / `order_file_path`）迁移到 sqlite 订单日志（见第七节）。
记录模型定义于 `storage/records.py`，表结构定义于 `storage/schema.py`（SQLAlchemy Core 元数据，单一来源），
语句与事务由 `storage/order_journal.py` 实现（设计见 `.memory/03-coding-design.md`）。
策略通过 `strategy_key` 归属订单：一个数据库多策略共享，`strategy_key` 是策略的稳定标识（如
`dogeusdt_long_buy_1m`），替代原先的文件路径。

## 七、订单日志（sqlite，storage/order_journal.py）

两张表（原 `order_ids`（WAL）已删除，职责由 `orders.status` 为空承担）。

### 逻辑仓位表 `grid_positions`

一行 = 一个 `GridPosition` 的持久化形态（`GridPositionRow`），只存策略参数与运行时状态：

| 列 | 说明 |
| --- | --- |
| `id` | TEXT PK，逻辑仓位号（journal 生成，与首单同事务写入） |
| `strategy_key` | 归属策略（索引） |
| `symbol` | ccxt 格式交易对（接管建仓时可能暂无订单，需自带） |
| `position_side` | `long` / `short`：决定入场/出场归类 |
| `main_order_side` | 网格主攻方向；不参与入/出场归类 |
| `opening_quantity` / `opening_price` | 开仓基准：接管持仓时的数量与均价（普通网格数量为 0）；`opening_price` 同时作为「尚无成交时的参考价」（网格间隔 / 止盈判定基准） |
| `fixed_take_profit_rate` / `signal_min_take_profit_rate` | 止盈参数 |
| `enable_stop_loss` / `stop_loss_rate` | 止损参数（**活参数**）：固定止损按 `仓位均价 × (1 ∓ stop_loss_rate)` 实时判定，修改（改库或改配置）后重启即生效，无需改其它字段 |
| `enable_trailing_stop` / `trailing_stop_rate` / `trailing_stop_activation_rate` / `trailing_stop_price` | 跟踪止损参数与运行时状态（`trailing_stop_price` 只归属跟踪止损：开仓时以 `收盘价 × (1 ∓ stop_loss_rate)` 播种为 ratchet 下限，每根 K 线只向上抬升，不可派生，必须落库；**固定止损不读此字段**） |
| `active` | 1 = 未到终态；0 = 已归档。**唯一物化的状态标记**，其余相位派生 |
| `closed_reason` | 归档原因（`PositionCloseReason`，TEXT 可空）：活跃仓位为 `NULL`，归档时与 `active=0` 同语句写入；`NULL` 的归档行 = 历史归档（该列引入前），仅作审计展示、不参与逻辑判定 |
| `created_at` / `updated_at` | 时间戳 |

### 订单表 `orders`

一行 = 一次委托（**先落行、再发交易所**）：

| 列 | 说明 |
| --- | --- |
| `order_id` | TEXT PK，custom_id（交易所返回为准） |
| `grid_position_id` | 归属逻辑仓位（索引） |
| `strategy_key` | 归属策略 |
| `symbol` | ccxt 格式交易对 |
| `side` / `type` | 方向与订单类型 |
| `price` / `quantity` / `filled_qty` / `avg_price` | 委托价量与成交 |
| `status` | 订单状态（`OrderStatus`）；**为空 = 结果未确认**，承担 WAL 标记与对账线索 |
| `created_at` / `updated_at` | 时间戳 |

### 入/出场归类（原 `OrderRole` 已删除）

`entry_side = buy if position_side == long else sell`，`exit_side` 反之。
`orders.side == entry_side` 即入场单，`== exit_side` 即出场单。

### 派生量（不落库，`GridPosition` 计算属性）

- `entry_filled = Σ entry.filled_qty`；`entry_pending = Σ(entry.quantity − entry.filled_qty)`（仅 `status=OPEN` 的行）
- `exit_filled = Σ exit.filled_qty`；`has_pending_exit = 存在 status ∈ {OPEN, 空} 的 exit 行`
- `quantity = opening_quantity + entry_filled − exit_filled`
- `price = 加权均价(opening_price × opening_quantity, entry 行 avg_price × filled_qty)`
- `realized_pnl = (出场均价 − 入场均价) × 出场成交量 × 方向符号`（多头 +1 / 空头 −1）：
  平均成本法，入场成本含 `opening_quantity × opening_price`（接管持仓）；成交单价优先
  `avg_price`、缺失退回 `price`；未出场部分不计入（浮动盈亏需外部最新价）
- 相位：`active=0 → closed`；`quantity<=0 → opening`；`quantity>0 且 has_pending_exit → closing`；否则 `holding`

还原入口：`load_positions` 只取 `active=1`；`load_all_positions` 含已归档（历史盈亏统计用）。

### 一致性语义

- **下单前先写库**：首单与仓位行同事务（避免孤儿）；订单行 `status` 为空 = 结果未确认；
- 发送后回写 `status/filled_qty/avg_price`；
- 交易所明确拒绝/取消（订单曾存在过）→ 更新该行 `status` 并保留，**另起一行新 `order_id`** 重试；
- **交易所从未存在的订单**（GTX 被拒 -5022、对账查无）→ 删除预写行，且该 `order_id` **可复用**重试
  （同一 id 不堆多行）；一旦订单存在（被接受 / 有成交）则 id **作废**，必须换新 id；
- **`status` 为空 ⇔ 结果未确认**，即对账清单；对账用交易所事实回写该行；
- 归档不删行（`active=0`）；`orders` 不随 `sync` 删除。

## 八、信号与规则层

### 1. 四层职责

| 层 | 只做 | 不做 |
| --- | --- | --- |
| `Signal` | 回答"行情现在是什么状态" | 不含持仓语义、无入场/离场概念 |
| `EntryGate` | 逐条独立判断某个入场条件是否满足 | 不构造仓位、不下单 |
| `ExitPolicy` | 把行情状态 + 止盈止损组合成离场决策 | 不下单、不撤单、不归档 |
| `SignalGridStrategy` | 装配闸门/策略 + 仓位与订单管理 | **不做任何入场/离场判定**（不出现方向比较） |

依赖方向单向：`strategy` → `entry_gate`/`exit_policy` → `model`；gate/policy 不得 import 任何
策略模块。设计理由见 `03-coding-design.md` 第三节。

### 2. `SignalKind` 语义对照

| SignalKind | 行情方向含义 | 替代的旧实现 |
| --- | --- | --- |
| `TRIGGER` | 无方向倾向：任何非 `NEUTRAL` 信号都触发入场与离场 | `AlphaTrendGridsSignal`（已删除） |
| `DIRECTIONAL` | 有方向倾向：同向许可入场、反向许可离场 | `AlphaTrendSignal(side=...)`（`side` 已删除） |

`signal: Signal | None` 时 **不做任何方向许可**（放行）。注意这不是"中性信号" ——
空信号放行，中性信号拦截，两者语义相反，因此**不能**用 `NoSignal` 空对象替代可空引用。

### 2.1 信号实现

| 实现 | 输出 | 说明 |
| --- | --- | --- |
| `AlphaTrendSignal` | 翻转根的原始方向 | 事件型：只在方向翻转根产出 `LONG` / `SHORT`，其余 `NEUTRAL` |
| `InvertedSignal(inner)` | 内层方向反转 | 组合包装器；`NEUTRAL` 保持中性；入场与离场共享同一 `MarketSignal`，因此同步反向 |
| `None` | —— | 不做方向许可（见上） |

反向信号只反转方向，不改变内层状态与去重语义；单配置仍只持一个方向
（多空双向反手需 long / short 两个配置）。

### 3. 入场闸门（`EntryGate`）

装配顺序与既有判定顺序一致：

| 闸门 | 规则 | 判定口径 |
| --- | --- | --- |
| `MaxPositionsGate` | `not limit.reached(len(positions))` | `>=` |
| `SignalTriggerGate` | `signal is None or direction is not NEUTRAL` | —— |
| `DirectionalSignalGate` | `signal is None or direction == of_order_side(entry_side)` | —— |
| `PriceRangeGate` | `close is not None and lowest <= close <= highest` | **闭区间**（含等号） |
| `GridSpacingGate` | 无仓位直接放行；否则极值仓位 `profit_and_loss_ratio(close) <= -grid_spacing_rate` | **`<=` 含等号**；**正负号改变语义，见下** |
| `TrendSpacingGate` | 无仓位直接放行；否则最外层仓位（盈利方向极值）`profit_and_loss_ratio(close) >= trend_spacing_rate` | **`>=` 含等号**；**顺势阶梯，见 3.2** |
| `MaxOrderStopLossGate` | `not limit.stop_loss_triggered(len(positions))` | —— |

`EntryGateChain` 短路 AND，空链恒放行。

### 3.1 `grid_spacing_rate` 的正负号语义

判定式 `profit_and_loss_ratio(close) <= -grid_spacing_rate`，其中 `ratio` 带符号
（亏损为负、盈利为正）。因此**间距的正负号决定它是单向限制还是双向距离限制**：

| `grid_spacing_rate` | 判定 | 语义 |
| --- | --- | --- |
| 正值（如 `0.01`） | `ratio <= -0.01` | **单向**：仅亏损方向走出间距后加仓（逆势下跌加仓）；盈利方向一律拦截 |
| 负值（如 `-0.1`） | `ratio <= 0.1` | **双向距离限制**：只要相对基准仓位的涨跌幅在 \|rate\| 以内就加仓 —— 顺势下跌与逆势上涨小幅都会放行；超出距离才拦截 |

以基准价 0.5、间距 ±10% 为例：

| close | ratio | `rate=0.01` | `rate=-0.1` |
| --- | --- | --- | --- |
| 0.45 | -0.10 | 放行 | 放行 |
| 0.50 | 0.00 | 拦截 | 放行 |
| 0.51 | +0.02 | 拦截 | 放行 |
| 0.55 | +0.10 | 拦截 | 放行（恰好等于） |
| 0.60 | +0.20 | 拦截 | 拦截（超出距离） |

`template/qqqusdt.py` 的 `grid_spacing_rate=-0.1` 是**有意的双向距离限制**，
不是待修的缺陷。该语义由 `tests/test_entry_gate.py` 的
`test_negative_spacing_*` / `test_positive_spacing_*` 五个测试锁定。

### 3.2 `TrendSpacingGate`（顺势阶梯加仓）

放行规则：

1. **只有一层已成交仓位**：无条件放行第二层 —— 不论新价格在成本价上方
   （顺势）还是下方（逆势）。
2. **两层及以上**，满足任一即可：
   - **顺势间距**：`profit_and_loss_ratio(close) >= trend_spacing_rate`（含等号），
     基准取**最外层仓位**（盈利方向极值：多头取最高价层、空头取最低价层）；
   - **最外层回踩带**：按价格高低取**最外层两层** `quantity > 0` 的仓位均价
     `p1, p2`，收盘价落在 `[min(p1, p2), max(p1, p2)]`（闭区间）内即放行。
     含义是"小回踩不破最外层两层开仓价"仍属于顶部加仓区，按加仓成功处理，
     而不是失败层。

无仓位直接放行（第一层不受间距约束）。

示例（多头）：已有 `100, 101, 103, 106`，信号价 `105` 落在最外层两层
`103..106` 之间 → 放行，新增 `105`，持仓变为 `100, 101, 103, 105, 106`。
只有一层时：已有 `100`，新价格 `99` → `99, 100`；新价格 `101` → `100, 101`。

"最外层两层"按**价格高低**取（多头最高两层、空头最低两层），不按创建顺序；
零数量层（未成交 / 已耗尽）不参与。

**优先级**：策略仍是"先平后开"——同一根完成 K 线若反向信号触发信号止损
（浮亏层 ≥ 2），本根先批量止损，回踩带不再加仓；回踩带只过滤"只有一层浮亏"
的噪声场景。

- 与 `GridSpacingGate` 的**方向相反**：后者基准是亏损方向极值层（多头取最低价层），
  只放行"走出亏损间距"的加仓（逆势补仓）；前者只放行"顺盈利方向再加一层"的加仓。
- 二者**语义互斥**（同一链内 AND 组合会把绝大多数行情拦死），由
  `SignalGridStrategyConfig.trend_spacing_rate` 二选一装配：非空 → 装配
  `TrendSpacingGate` 并替换 `GridSpacingGate`；为空 → 保持 `grid_spacing_rate` 语义。
- "层"的节奏由编排层保证（每根完成 K 线最多建一层）；加仓的**方向许可**由信号闸门
  决定 —— `AlphaTrendSignal` 是翻转事件信号，若需"趋势延续中每隔 X% 加一层"，
  需配合状态型信号（趋势持续期间恒输出方向）。

该语义由 `tests/test_entry_gate.py` 的 `test_trend_spacing_*` 与
`tests/test_signal_grid_rules.py` 的装配契约测试锁定。

### 4. 离场策略（`ExitPolicy`）

| 策略 | 规则 | force |
| --- | --- | --- |
| `ForceExitPolicy` | `trigger()` 为真（达到上限止损 / 手动 `close_position`） | **True** |
| `StopLossPolicy` | 读仓位的 `enable_stop_loss` / `enable_trailing_stop` / `stop_loss_rate` / `trailing_stop_price` / `price` / `entry_side` | **True** |
| `LayeredReducePolicy` | 反向信号命中，且 `quantity > 0` 的浮亏层（`ratio < 0`）**至少两层**；对**全部浮亏层**产出离场决策 | **True** |
| `FixedTakeProfitPolicy` | `profit_level(close) == 2` | False |
| `SignalExitPolicy` | 信号反向命中且自身**浮盈**（`profit_and_loss_ratio(close) > 0`） | False |

`ExitPolicyChain` 合并规则：`should_exit = any`、`force = any`，**合并顺序无关**。

`RestingTakeProfitPolicy` 不进链：它只提供盘中休息限价止盈单的挂单价，不判断是否离场。

**`force` 不变量**：已有未成交出场单时，只有 `force=True` 的决策可覆盖（撤单结算后重挂）；
普通止盈保留原挂单，不重复下单。`LayeredReducePolicy` 是第三个 force 来源：逆势减仓
必须实际减仓，每根完成 K 线撤单重挂到最新收盘价（否则下跌途中限价单挂着不成交，
逐层推进停滞）。

**信号离场按盈亏状态分派**（Step 24，取代"两个独立策略可并存"的中间形态）：
反向信号命中时，**同一层**按其盈亏状态走且仅走一个分支：

| 仓位状态 | 开关 | 策略 | 行为 |
| --- | --- | --- | --- |
| 浮盈（`close` 在成本价盈利侧，`profit_and_loss_ratio(close) > 0`） | `enable_signal_take_profit`（默认 True） | `SignalExitPolicy` | 止盈离场（非 force，保留原挂单） |
| 浮亏（`profit_and_loss_ratio(close) < 0`） | `enable_signal_stop_loss`（默认 False） | `LayeredReducePolicy` | 止损离场（**全部浮亏层一次性** force 撤单重挂；浮亏层 **< 2 层不触发**） |
| 持平（`ratio == 0`，含均价不可用） | —— | 均不触发 | 保持持仓 |

- 两分支按盈亏符号**天然互斥**，同层永不冲突，`force` 合并规则退化为兜底；
- 止盈无盈利门槛（`exit_signal_take_profit_min_rate` 已删除）—— 有浮盈即为止盈；
- 止损要求**至少两层浮亏**才触发（单层小浮亏在震荡中是噪声，触发会制造磨损）；
  触发后**所有浮亏层一次性离场**，浮盈/持平层不动（不变量见上文）；
- 全关 = 无信号驱动离场（只剩固定止盈 / 价格止损 / 上限止损）。

### 5. 止损参数与止盈阈值的权威来源

止损与止盈参数**随 `GridPositionRow` 落库**，由策略在建仓时从 config 写入，
`ExitPolicy` 一律只读 `position.*`：

- `StopLossPolicy` 读 `position.enable_stop_loss`（总开关）与
  `position.enable_trailing_stop`（模式选择）：**固定止损**（未开跟踪）按
  `仓位均价 × (1 ∓ stop_loss_rate)` 实时计算触发价（`stop_loss_rate` 是活参数）；
  **跟踪止损**（开了跟踪）读 ratchet 字段 `trailing_stop_price`（缺失时不触发）；
- `FixedTakeProfitPolicy` 读 `position.profit_level(close)`（档位 2 用 `fixed_take_profit_rate`）；
- `SignalExitPolicy` 读 `position.profit_level(close)`（档位 1 用 `signal_min_take_profit_rate`）。

因此 `SignalExitPolicy` **不需要**持有 `min_take_profit_rate` 参数 —— 避免参数源分裂成两份。
`RestingTakeProfitPolicy` 的挂单价同样取 `position.fixed_take_profit_rate`（与
`profit_level` 同源），config 中的同名字段只在建仓时读一次。

### 6. 已删除的领域概念

| 已删除 | 原因 |
| --- | --- |
| `Signal.side` | 把信号方向与持仓方向压在同一个 `OrderSide` 上，见 `03-coding-design.md` 三.2 |
| `AlphaTrendSignal.reverse` | 死参数（模板从未传），且只反转输出不反转内部状态 |
| `AlphaTrendGridsSignal` | `is_entry == is_exit` 推翻基类语义，语义由 `SignalKind.TRIGGER` 承载 |
| `position_reverse` | 反向持仓开关导致 `side`/`master_side`/`position_side` 三者语义缠绕；且它与负 `grid_spacing_rate` 两套机制表达同一意图。删除后方向直接由 config 声明，反向/双向加仓改由负间距表达（见 3.1） |

`position_reverse` 删除后，方向直接来自 config：`position_side` 即实际持仓方向，
`master_side` 即实际入场方向，`GridPositionRow.main_order_side` 与 `position_side` 同值
（`main_order_side` 只表示网格主攻方向，**不参与**入/出场归类）。

## 九、只读 API 视图（P0）

API 响应对象定义于 `smart_trader/api/schemas.py`，**全部是派生视图，不落库、不新增表**：

| 视图 | 来源 | 说明 |
| --- | --- | --- |
| `OrderView` | `OrderRecord` + 所属仓位的 `position_side` | `role` 由 `entry_side` / `exit_side` 派生；`unconfirmed = status is None`（WAL） |
| `PositionView` | `GridPosition` | `phase`、`quantity`、`price`、`realized_pnl` 直接取计算属性；`stop_loss_price` / `take_profit_price` 由均价与费率派生 |
| `PositionDetailView` | `PositionView` + 关联订单 | 详情页使用 |
| `StrategySummaryView` | 同 `strategy_key` 的仓位 + 订单聚合 + 运行态心跳 | 活跃/归档仓位数、已实现盈亏、挂单数、未确认数、最后更新时间；`timeframe` / `alive` / `running` / `heartbeat_at` / `reconcile` 来自心跳文件（账本不含 timeframe 与运行态） |
| `ReconcileView` | 心跳文件中的 `ReconcileSnapshot` | `exchange_qty` / `local_qty` / `gap` / `checked_at` / `tracked_exit_orders` / `max_exit_query_failures` / `failing_exit_orders`；只读，不落库 |
| `OverviewView` | 全库聚合 | 计数、盈亏合计、策略摘要、告警（未确认订单） |

派生规则：

- `stop_loss_price`：`enable_stop_loss=False` → `None`；跟踪止损 → `trailing_stop_price`
  （缺失为 `None`）；固定止损 → 均价 `× (1 - stop_loss_rate)`（多头）/
  `× (1 + stop_loss_rate)`（空头）—— 与 `StopLossPolicy` 的实时口径一致，不读死快照；
- `take_profit_price`：均价 `× (1 + fixed_take_profit_rate)`（多头）/
  `× (1 - fixed_take_profit_rate)`（空头），仅作展示，不代表开关已启用；
- `entry_pending`：`pending_entry_orders` 的 `quantity - filled_qty` 之和（仅未了结入场单）；
- 浮动盈亏不在后端计算：后端没有最新价，P0 前端不展示未实现盈亏。

**对账视图**：`gap = local_qty - exchange_qty`。`gap < 0` 表示交易所持仓多于本地
（手工加仓 / 来源不明），必须作为 critical 告警人工处置；`gap > 0` 由策略按最外层优先
内部归档 / 扣减，属于既有收敛路径。`max_exit_query_failures` 是单个出场单的连续查询
失败次数，达到 3 次生成 warning；查询成功后计数清零。心跳过期时整个 `reconcile` 为
`None`，不展示旧的差额数据。

## 十、控制命令（P1，`commands` 表）

控制面与交易账本分离：`commands` 是**输入队列 + 审计日志**，`grid_positions` / `orders` 仍是账本唯一事实。

| 列 | 说明 |
| --- | --- |
| `id` | TEXT PK，`CMD` + 随机串 |
| `strategy_key` | 目标策略；`NULL` 表示全局（仅 `kill_switch`） |
| `action` | `CommandAction`：`pause` / `resume` / `close_all` / `cancel_entries` / `kill_switch` |
| `status` | `CommandStatus`：`pending` / `running` / `succeeded` / `failed` |
| `params` | JSON 文本，预留参数位（P1 固定 `{}`） |
| `result` | 执行结果 / 错误摘要 |
| `requested_by` | 发起方（P1 固定 `web`） |
| `idempotency_key` | 可空唯一键，前端防重复提交 |
| `requested_at` / `started_at` / `finished_at` / `updated_at` | UTC 时间戳 |

状态机：`pending → running → succeeded | failed`。认领是条件 UPDATE，只有 `pending` 才能变 `running`；
执行异常由 `CommandRunner` 捕获、记录 `failed`，轮询线程继续运行。

命令语义见 `01-system-architecture.md` 第十三节。`close_all` 是边沿触发标志：
策略在下一根完成 K 线消费，`is_running=False` 时也会执行（kill switch 依赖）；
`cancel_entries` 只撤未成交入场单，归零仓位按 `ENTRY_DISCARDED` 归档。

### 人工仓位声明（P3，`position_acks` 表）

对账差额的操作者声明，**不参与交易、不写入账本**：

| 列 | 说明 |
| --- | --- |
| `id` | TEXT PK，`ACK` + 随机串 |
| `strategy_key` / `symbol` / `position_side` | 声明归属；来自创建时的运行态心跳 |
| `external_qty` | 声明为人工仓位的数量；必须 ≤ 当前交易所多于本地的差额 |
| `note` | 操作者备注 |
| `status` | `active` / `revoked`；同一策略同时最多一条 active |
| `acknowledged_by` / `acknowledged_at` | 创建人 / UTC 时间 |
| `revoked_at` / `revoked_by` | 软撤销审计 |
| `updated_at` | UTC 时间 |

判定：`surplus = exchange_qty - local_qty`（交易所多于本地为正）。
声明覆盖 `surplus` 时静默；`surplus > external_qty` 时对未解释差额继续告警；
`external_qty > surplus` 或 `surplus <= 0` 时声明失效（`position_ack_stale`）。
创建前要求策略心跳新鲜且有成功对账数据；新建声明自动撤销旧 active 声明，撤销幂等。

## 十一、行情图表视图（P2）

定义于 `smart_trader/api/schemas.py`，**不落库**：

| 视图 | 来源 | 说明 |
| --- | --- | --- |
| `CandleView` | `MarketDataPort.fetch_ohlcv` | `timestamp` 为毫秒；`finished` 表示该根是否收盘 |
| `IndicatorPoint` | 复用 `alpha_trend_indicator` 的输出 | `alpha_value` / `trend` / `is_breakout`，与 K 线按索引对齐 |
| `MarketChartView` | 上述两者 | 另含 `direction`（由最后 `trend` 符号派生）、`last_price`、`last_breakout_timestamp` |

- 指标参数固定为 ATR 1.0 / period 8，与 `AlphaTrendSignal` 默认值一致；若策略改参，图表需同步，
  因此不把参数写进前端。
- `direction` 只是指标趋势方向，不等于策略的 `MarketSignal`（后者只在翻转根产出信号）。
- 行情不写入 SQLite：P2 以公共接口按需拉取，避免 K 线写放大与保留策略问题。

## 十二、策略配置（P3，`strategy_configs` 表）

策略参数从代码模板迁移到数据库，一行即一个策略实例：

| 列 | 说明 |
| --- | --- |
| `strategy_key` | TEXT PK，账本归属键，创建后不可变 |
| `payload` | `StrategyConfigPayload` 的 JSON 文本 |
| `version` | 乐观锁版本，从 1 递增 |
| `enabled` | 启动时是否加载；无删除，只能停用 |
| `updated_by` | 最后修改方（当前固定 `web`） |
| `created_at` / `updated_at` | UTC 时间戳 |

`StrategyConfigPayload`（`strategy/config_payload.py`）是**纯参数模型**，不含 `strategy_key`：

- 身份 / 数据源：`symbol`（ccxt 形式，规范化为 `BASE/QUOTE`）、`timeframe`、
  `data_source`（`ws` / `timer`）；
- 信号：`signal_type`（`alpha_trend` / `alpha_trend_inverse` / `none`）、`atr_multiple`、`period`、`signal_kind`；
- 方向：`position_side`、`master_side`；
- 网格：`per_order_amount`、`per_order_unit`、`grid_spacing_rate`、
  `trend_spacing_rate`、`max_order`、`highest_price`、`lowest_price`；
- 止盈止损：`enable_signal_take_profit`、`enable_signal_stop_loss`、
  `fixed_rate_take_profit`、`fixed_take_profit_rate`、`take_profit_use_limit_order`、
  `enable_order_stop_loss`、`order_stop_loss_rate`、`enable_max_order_stop_loss`、
  `paused_after_stop_loss`、`enable_trailing_stop`、`trailing_stop_rate`、
  `trailing_stop_activation_profit_rate`；
- 执行：`place_order_behavior`。

转换：`to_strategy_config(strategy_key)` 物化 `Symbol` 与
`AlphaTrendSignal` / `InvertedSignal(AlphaTrendSignal)` / `None`；
`from_strategy_config(config, data_source)` 供模板播种。
不可序列化的 `Signal` 实现只允许上述三种，其它实现直接报错。

**单层规模（Step 35）**：`per_order_amount` + `per_order_unit` 是唯一来源。
`base` 直接作为币数量（与价格无关）；`quote` 在开仓时按信号收盘价折算
（`amount / close`），经 `SymbolInfo.format_qty` 按 `step_size` 向下取整，
并校验最小数量 / 最小名义价值。折算发生在策略层、创建仓位行之前：
校验失败抛 `SmartTraderError`，不写仓位 / 订单行。`amount=0` 仍表示空仓位。
`quote` 表达的是订单名义价值（价格 × 数量），不是保证金。

护栏：`symbol` / `timeframe` / `data_source` / `position_side` / `master_side`
创建后不可修改（API 409）；活跃仓位存在时禁止停用；其余字段可改，
保存后由运行时轮询热更新。

**热更新边界（Step 36）**：运行时每 2 秒读取配置表。新增启用配置热加载；
现有配置版本变化且身份字段不变时，`SignalGridStrategy.apply_config` 原地更新参数、
重建闸门 / 离场链，保留仓位、订单与信号状态。`symbol` / `timeframe` /
`position_side` / `master_side` / `data_source` **创建后不可修改**（API 409）；
停用仍需重启，启用未运行配置走热新增。
