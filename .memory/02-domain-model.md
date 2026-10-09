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
| `GridPosition` | `strategy/signal_grid_strategy.py` | **逻辑仓位**：一个格子的完整生命周期；只持策略参数与运行时跟踪状态，价格/数量/相位全部由订单派生 |
| `GridPositionRow` | `storage/records.py` | `grid_positions` 表的一行：`GridPosition` 的持久化形态，不含策略行为 |
| `OrderRecord` | `storage/records.py` | `orders` 表的一行：一次委托（`status` 为空表示结果未确认，承担 WAL 标记） |

`GridPosition` 的**数量、均价、相位都不是存储字段**，而是由该仓位关联的 `orders` 派生
（定义见第七节）。入场/出场按 `position_side` 归类：`long` 下 buy=入场、sell=出场，
`short` 反之；`main_order_side` 只表示网格主攻方向，**不参与**入/出场归类。
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
| `enable_stop_loss` / `stop_loss_rate` | 止损参数 |
| `enable_trailing_stop` / `trailing_stop_rate` / `trailing_stop_activation_rate` / `trailing_stop_price` | 跟踪止损参数与运行时状态（`trailing_stop_price` 每根 K 线更新，不可派生，必须落库） |
| `active` | 1 = 未到终态；0 = 已归档。**唯一物化的状态标记**，其余相位派生 |
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
- 相位：`active=0 → closed`；`quantity<=0 → opening`；`quantity>0 且 has_pending_exit → closing`；否则 `holding`

### 一致性语义

- **下单前先写库**：首单与仓位行同事务（避免孤儿）；订单行 `status` 为空 = 结果未确认；
- 发送后回写 `status/filled_qty/avg_price`；
- 交易所明确拒绝/取消（订单曾存在过）→ 更新该行 `status` 并保留，**另起一行新 `order_id`** 重试；
- **交易所从未存在的订单**（GTX 被拒 -5022、对账查无）→ 删除预写行，且该 `order_id` **可复用**重试
  （同一 id 不堆多行）；一旦订单存在（被接受 / 有成交）则 id **作废**，必须换新 id；
- **`status` 为空 ⇔ 结果未确认**，即对账清单；对账用交易所事实回写该行；
- 归档不删行（`active=0`）；`orders` 不随 `sync` 删除。
