# 编码设计

## 强制流程

- TDD 红-绿-重构; 7 类必测场景 (正常/边界/隔离/事务/优先级/幂等/空值)
- 三重验证: `uv run pytest` / `uv run pyright` / `uv run ruff check .`
- 代码变更后同步更新受影响测试 (测试是可执行文档, 不一致的测试比没有测试更危险)

## 交易所 I/O 防御模式 (v2 状态机)

- `query_order` 契约: 返回 None = 订单不存在; 其他异常向上抛
- sync 逻辑整体包裹 try/except: 查询/解析失败 → 保持原状态, 下根K线重试 — 查询失败绝不改变账本
- `_cancel_order_safe`: 撤单失败 (已成交/已撤销/网络) 只记日志, 不中断当根K线
- 提交结果未知原则 (超时/响应丢失): 订单可能已到达交易所, 一律以策略生成的已知ID入账
  (入场 → ENTRY_PENDING, 平仓/止盈 → EXIT_PENDING), 状态机下根K线核实 —
  查无此单则自然完结/回持仓态, 已存在则直接接管; 绝不"当作没发生过"而重下新单
- custom_id 全链路复用: 策略生成 → place_order_v2 → 追单器所有重试沿用同一ID;
  追单器对 InvalidOrder/未知异常先查单确认是否已存在, 存在则接管, 杜绝孤儿单
- `record_orders` 去重: 只有仍存在于账本的订单才会移入历史, 防止双路径重复记录
- DataEventLoop 对 Future 挂 done-callback: 任务异常记录 ERROR 日志, 不再被线程池吞掉

## 可测性

- 交易所客户端用 `Mock(spec=ExSwapClient)`; `order_file_path=""` 关闭文件 IO
- PositionReconciler 注入 `clock: Callable[[], float]`, 测试用假时钟验证节流
- 状态推进以"每根完成K线"为节拍, 测试直接构造 latest_kline 驱动

## 已知限制 (设计取舍)

- 平仓单挂单后不主动改价/撤换 (旧逻辑每根K线撤单重挂): 由状态机等待成交确认, 避免 cancel-成交竞态
- 单笔止损价在入场单创建时按当时 close 计算, 与入场实际成交价略有偏差
- EXIT_PENDING 部分成交期间, 预期持仓按全量计算, 对账可能出现瞬时误报 (下个周期自愈)
- 平仓优先于开仓: 本根K线有平仓动作 (exited/removed) 就不再开新仓
- 策略暂停 (is_running=False) 时状态机与对账仍然运行, 用于确认在途平仓单
