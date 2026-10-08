# 领域模型

## 一、核心对象

| 对象 | 位置 | 说明 |
| --- | --- | --- |
| `Symbol` | `model.py` | 交易对。`base`/`quote` 只是数据，格式转换由 `ccxt()` / `binance()` / `from_ccxt()` 提供 |
| `SymbolInfo` | `model.py` | 交易所交易规则：tick/step/最小最大价量/最小名义价值；负责价格数量格式化与校验 |
| `Kline` | `model.py` | K 线，`finished` 表示该根是否已收盘 |
| `OrderRequest` | `model.py` | 下单请求（领域对象） |
| `OrderResult` | `model.py` | 订单结果（领域对象），`place`/`query`/`cancel` 的统一返回 |
| `Position` | `model.py` | 持仓 |
| `GridPosition` | `strategy/signal_grid_strategy.py` | **逻辑仓位**（原 `Order`，正名）：网格的一个格子从入场到平仓的完整生命周期，含止盈/止损参数；与交易所订单（`OrderResult`）解耦 |
| `GridPositionRow` | `storage/records.py` | `grid_positions` 表的一行：`GridPosition` 的持久化形态，不含策略行为 |
| `OrderRecord` | `storage/records.py` | `orders` 表的一行：一次真实委托的流水记录（审计/统计） |
| `IdRecord` | `storage/records.py` | id 表的一行：预生成的订单号 + 确认状态 + 意图快照 |
| `OrderIntent` | `storage/records.py` | 下单意图快照：崩溃后对账重建订单所需的最小上下文 |

`GridPosition` 的 `quantity` 是委托量，`filled_quantity` 是**入场单实际成交量**；
平仓数量取 `effective_filled_quantity`（见 `01-system-architecture.md` 第十节不变量）。
模型配置 `extra="forbid"`，字段拼写错误会立即报错而不是被静默忽略。

## 二、枚举（统一定义于 `model.py`，禁止硬编码字符串）

| 枚举 | 取值 |
| --- | --- |
| `OrderSide` | `BUY` / `SELL` |
| `PositionSide` | `LONG` / `SHORT` |
| `OrderType` | `LIMIT` / `MARKET` |
| `TimeInForce` | `GTC` / `IOC` / `FOK` / `GTX`(post only) |
| `OrderStatus` | `OPEN` / `CLOSED` / `CANCELED` / `REJECTED` / `EXPIRED` |
| `PlaceOrderBehavior` | `NORMAL` / `CHASER`(追单并等成交) / `CHASER_OPEN`(追单只挂单) |
| `ErrorCode` | `exceptions.py`，见第五节 |

## 三、订单字段

### OrderRequest

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `custom_id` | `str` | 客户端订单号，幂等标识 |
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

## 四、ccxt 翻译表（只在适配器内使用）

| 领域 | ccxt 统一字段 | 备注 |
| --- | --- | --- |
| `OrderRequest.custom_id` | `params.newClientOrderId` | |
| `OrderRequest.position_side` | `params.positionSide` | 双向持仓模式 |
| `OrderRequest.time_in_force` | `params.timeInForce` | `GTX` 即 post only |
| `OrderResult.status` | `order.status` | `open`/`closed`/`canceled`/`rejected`/`expired`；其余（如 `canceling`）→ `ORDER_RESPONSE_INVALID` |
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

## 六、持久化

订单持久化已从 JSON 文件（`OrderRecorder` / `order_file_path`）迁移到 sqlite 订单日志（见第七节）。
记录模型定义于 `storage/records.py`，表结构定义于 `storage/schema.py`（SQLAlchemy Core 元数据，单一来源），
语句与事务由 `storage/order_journal.py` 实现（设计见 `.memory/03-coding-design.md`）。
策略通过 `strategy_key` 归属订单：一个数据库多策略共享，`strategy_key` 是策略的稳定标识（如
`dogeusdt_long_buy_1m`），替代原先的文件路径。

## 七、订单日志（sqlite，storage/order_journal.py）

### id 表 `order_ids`（WAL）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 预生成的订单号（custom_id） |
| `used` | INTEGER | **0 = 结果未确认（待对账）**；1 = 已确认/已核销 |
| `type` | TEXT | 恒为 `'ORDER'`（预留扩展） |
| `strategy_key` | TEXT | 归属策略 |
| `intent` | TEXT | 意图快照 JSON（`OrderIntent`） |
| `created_at` / `updated_at` | TEXT | 时间戳（审计、对账最小年龄判断） |

### 逻辑仓位表 `grid_positions`

一行 = 一个 `GridPosition` 的持久化形态（`GridPositionRow`），字段平铺 + `active` 标记：

| 列 | 说明 |
| --- | --- |
| `entry_id` | TEXT PK，逻辑仓位号 = 入场单最终 custom_id（追单改写后以交易所返回为准） |
| `strategy_key` | 归属策略（索引） |
| `side` / `price` / `quantity` / `filled_quantity` | 方向与数量（`quantity` 委托量、`filled_quantity` 入场单实际成交量） |
| `fixed_take_profit_rate` / `signal_min_take_profit_rate` | 止盈参数 |
| `exit_price` / `exit_id` / `status` | 出场单与入场状态 |
| `stop_loss_rate` / `enable_stop_loss` / `trailing_stop_rate` / `enable_trailing_stop` / `trailing_stop_activation_profit_rate` / `current_stop_price` | 止损参数 |
| `active` | 1 = 当前仓位；0 = 已平仓归档（加载只取 active=1，归档可查询） |
| `created_at` / `updated_at` | 时间戳 |

### 订单流水表 `orders`

一行 = 一次"交易所确认存在过"的真实委托（`OrderRecord`），以 append 为主：

| 列 | 说明 |
| --- | --- |
| `order_id` | TEXT PK，custom_id（交易所返回为准） |
| `strategy_key` | 归属策略（索引） |
| `role` | `entry` / `exit`（`OrderRole`） |
| `symbol` | ccxt 格式交易对（如 `DOGE/USDC:USDC`） |
| `side` / `type` | 方向与订单类型 |
| `price` / `quantity` / `filled_qty` / `avg_price` | 委托价量与成交 |
| `status` | 订单状态（`OrderStatus`） |
| `created_at` / `updated_at` | 时间戳 |

被拒的尝试（-5022 等）**不进流水表**，保留在 id 表（审计留痕、防流水淹没）。

### OrderRole（原 `IntentKind`）

`ENTRY` / `EXIT`——下单意图与订单流水共用同一枚举。

### OrderIntent（意图快照）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kind` | `OrderRole` | `ENTRY` / `EXIT` |
| `symbol` | `Symbol` | 对账 query 必须带 symbol |
| `side` | `OrderSide` | 下单方向 |
| `order_type` | `OrderType` | 网格下单恒为 `LIMIT`，对账重建流水时使用 |
| `entry_position` | `GridPositionRow \| None` | `ENTRY`：重建仓位行的模板（策略参数来自这里） |
| `entry_ids` | `list[str]` | `EXIT`：该出场单关联的入场单号（对账时回填 exit_id） |

### 一致性语义

- `used=0` ⇔ 订单号结果未确认；对账时用「意图快照 + 交易所事实」补全仓位与流水；
- **挂上成功的写入三合一**（同一事务）：{ upsert `grid_positions` + INSERT `orders` 流水 + 置 `used=1` }；
- 明确拒绝直接核销（`used=1`），结果未知保持 `used=0` 留给对账；
- 仓位归档不删行；流水 append-only，`sync_positions` 不触碰流水表。
