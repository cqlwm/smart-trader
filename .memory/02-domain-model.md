# 领域模型

## Order (v2 账本订单)

| 字段 | 含义 |
|---|---|
| entry_id | 入场单 clientOrderId (追单会改写为交易所返回的真实ID) |
| side | 逻辑方向 (master_side, position_reverse 时与交易所方向相反) |
| price | 入场价 (以下单返回为准) |
| quantity | 持仓数量 (部分成交时按实际成交量扣减) |
| status | 入场单交易所状态快照 (open/closed/canceled...) |
| phase | 账本生命周期 (OrderPhase, 见下) |
| exit_id | 平仓单 clientOrderId (批量平仓共享同一ID) |
| exit_price | 平仓价 |
| fixed_take_profit_rate / signal_min_take_profit_rate | 固定止盈 / 信号止盈最小盈利阈值 |
| stop_loss_rate / enable_stop_loss / current_stop_price | 单笔止损 |
| trailing_stop_rate / enable_trailing_stop / trailing_stop_activation_profit_rate | 跟踪止损 |

## status 与 phase 的语义区分

- status: 入场单在交易所的原始状态快照, 只描述入场单本身
- phase: 账本视角的订单生命周期, 驱动策略行为 (平仓检查/间距锚点/对账口径)

## OrderPhase 状态机

```
ENTRY_PENDING ──入场成交──> HOLDING ──触发平仓且平仓单提交成功──> EXIT_PENDING ──平仓成交──> CLOSED(移入历史)
     │                        ^                                          │
     └──撤销/失效且无成交──> CLOSED      └──平仓单被撤销/不存在(按已成交量扣减 quantity)──┘
```

| 转换 | 触发 | 结果 |
|---|---|---|
| ENTRY_PENDING → HOLDING | sync 查询入场单已成交(含部分成交, quantity 修正为成交量, price 修正为交易所订单价) | 计入预期持仓 |
| ENTRY_PENDING → CLOSED | 入场单撤销/失效且无成交 / 在交易所不存在 | 移入历史, 无需平仓 |
| HOLDING → EXIT_PENDING | 止盈/止损/退出信号触发; 平仓单已提交 (含提交结果未知: 记录策略生成的 exit_id, custom_id 全链路复用) | 保留账本, 记录 exit_id |
| EXIT_PENDING → CLOSED | sync 查询平仓单已成交 (exit_price 修正为交易所订单价) | 移入历史 |
| EXIT_PENDING → HOLDING | 平仓单被撤销/不存在(按已成交量扣减 quantity) | 等待下次触发重新平仓 |

## 订单文件 (JSON)

- orders: 未完结订单 (ENTRY_PENDING / HOLDING / EXIT_PENDING)
- history_orders: 已完结订单 (CLOSED)
- is_reload: 用户置 True 触发热更新
- 旧文件迁移: 无 phase 字段的记录按已有字段推断 — 有 exit_id → EXIT_PENDING; status closed → HOLDING; 否则 ENTRY_PENDING

## 仓位对账

- 预期持仓 = Σ quantity(phase ∈ {HOLDING, EXIT_PENDING}) — 平仓在途的仓位在成交前仍然存在
- 实际持仓 = fetch_positions 按 symbol + 仓位方向过滤的 contracts 之和
- 不一致 → logger.error 告警, 不自动修复
- reconcile_interval_sec <= 0 关闭对账 (条件装配, 不设 enabled 双开关)
