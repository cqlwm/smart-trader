# 系统架构

## 一、分层与依赖方向

```
strategy / task / template              (业务编排: 网格、信号、止损)
        │
        ├──▶ execution (smart_trader/execution/)   执行编排: 追单、记账下单、对账
        │          │  │
        │          │  └──▶ storage (smart_trader/storage/)   订单日志: id 表 + order 表 (sqlite)
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
5. **storage 是纯数据层**：只做 sqlite 存取与事务，只依赖 `model`；不 import ccxt、不依赖 execution / strategy。
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
| `smart_trader/storage/order_journal.py` | 订单日志（sqlite）：id 表（WAL）+ `grid_positions`（逻辑仓位）+ `orders`（订单流水）+ 事务 |
| `smart_trader/execution/limit_order_chaser.py` | 追单执行策略 |
| `smart_trader/execution/journaled_trading.py` | 记账下单包装（TradingPort 装饰器）：预留 id → 下单 → 确认/核销 |
| `smart_trader/execution/order_reconciler.py` | 启动对账：扫未确认 id → 查交易所 → 补全/核销 |
| `smart_trader/strategy/` | 策略基类与网格策略 |
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

## 九、事件循环与任务

- `Task` 必须提供 `name`（用于日志定位），并统一经 `Task.safe_run` 调度：
  任务异常在此被捕获并记录，不会中断事件循环，也不会被线程池静默吞掉。
- 消息解析是纯函数（如 `parse_kline_message`），无法解析时返回 `None` 并记 debug 日志，
  不允许向上抛异常。
- `StrategyTask` 按 `symbol + timeframe` 过滤消息，非本任务的 K 线直接忽略。

## 十、持仓记账不变量

1. **平仓数量只能取实际成交量**：`GridPosition.effective_filled_quantity`
   （`CLOSED` 状态缺失成交量时按委托量兜底，兼容旧数据热加载）。
   任何"按委托量平仓"的写法都会在部分成交场景下超卖。
2. **`OrderStatus.CLOSED` 等价于完全成交**：ccxt 把部分成交映射为 `open`，
   因此适配器在 `closed` 且无 `filled` 字段时按委托量补齐成交量。
3. **未成交的订单不产生平仓单**：成交量 > 0 才会进入平仓队列。

## 八、测试策略

- 内存实现优先：`tests/fakes.py` 提供 `FakeCcxtExchange` / `FakeTradingPort` / `FakeMarketDataPort`，默认测试不触网（集成测试标记 `@pytest.mark.integration`）。
- 端口契约测试通过假端口验证业务行为，适配器测试通过假 ccxt 验证翻译规则。
- sqlite 日志测试统一使用 `:memory:` 数据库，不落盘、不共享状态。

## 十一、订单最终一致性（WAL + 对账）

背景：`place_order` 是"多步、可中断"的动作（预留 id → 发请求 → 记结果），任何一步崩溃都可能留下"交易所已有单、本地不知道"的幽灵订单。用"预写日志 + 启动对账"兜住：

### 流程（见 storage/order_journal.py）

1. **reserve 预留**：下单前先写 id 表——`INSERT {id, used=0, type='ORDER', strategy_key, intent=意图快照}`，拿到本次下单的 custom_id；
2. **place 下单**：用预留 id 调交易所（追单每次递进都要重新 reserve）；
3. **按结果收尾**：
   - 挂上成功 → **同一事务**内三合一：`upsert grid_positions（逻辑仓位）+ INSERT orders 流水 + UPDATE id SET used=1`；
   - 交易所明确拒绝（-5022 / 余额不足 / 精度等确定性失败）→ `used=1` 核销，不留仓位与流水；
   - **结果未知（网络超时、响应丢失、进程崩溃）→ 保持 used=0**，交给对账。

### 状态语义（不变量）

- **`used=0` ⇔ 该订单号结果未确认**（不是"没发过请求"）；id 表中 `used=0` 的记录就是待对账清单。
- **confirm 必须原子**：仓位行、订单流水与 used 置位必须同一事务，中间崩溃会永久丢单。
- **意图快照（intent）先于下单写入**：对账重建仓位行与流水所需的上下文（含止盈止损等策略参数）只能来自快照，交易所只提供客观事实。
- **订单流水只记"交易所确认存在过"的委托**（append-only）；被拒尝试留在 id 表，`sync_positions` 不触碰流水。

### 对账（storage 之外，execution/order_reconciler.py）

- 触发时机：策略启动加载仓位前（`force` 加载）；
- 规则：对每条 `used=0`：
  - 距预留时间过近（< 最小年龄）→ 跳过，留给下一次；
  - query 到有效订单（open / closed / 有任何成交）→ 用「意图快照 + 交易所事实」补全仓位与流水，`used=1`；
  - query 不到或无成交终结态 → `used=1` 核销。
- 对账查询异常单条容忍，不中断整个对账。

### 归谁调

- 记账包装 `JournaledTradingPort` 实现 `TradingPort`：`place` 内完成 reserve / confirm / void；`query` / `cancel` 透传。策略与追单器只依赖端口，不感知日志存在。
- 策略加载：`strategy_key` → `OrderJournal.load_positions`（替代原 `order_file_path` JSON）。
