# 系统架构

## 分层结构

```
run.py (装配入口, init_logging)
  └─ BinanceDataEventLoop (WebSocket → 线程池分发)
       └─ StrategyTask (K线解析与路由)
            └─ SignalGridStrategy (v2, 生产在用)
                 ├─ Signal (AlphaTrendGridsSignal, 入出场信号)
                 ├─ OrderManager (线程安全账本, 内含 OrderRecorder JSON 持久化)
                 ├─ LimitOrderChaserV2 (GTX 追单, 经 BinanceSwapClient)
                 └─ PositionReconciler (仓位对账, 只读告警)
```

- template/ 下按交易对组装策略实例; 同一交易对通常运行多空两个反转实例(position_reverse=True)
- grids_strategy_v3 已删除 (v2 引入状态机后语义分叉, 且从未接入运行)

## 每根完成K线的处理流水 (_on_kline_finished)

1. `load_orders`: 检查热更新 (用户把备份文件 is_reload 置 True)
2. `sync_order_phases`: 状态机同步 — 推进入场/平仓单真实状态 (暂停时也运行, 用于确认在途平仓单)
3. `_update_trailing_stops`: 更新持仓单跟踪止损价 (仅 is_running)
4. `check_close_order`: 止盈/止损/退出信号 → 提交平仓单 (EXIT_PENDING 留账本) / 撤销未成交入场单
5. `check_open_order`: 网格补仓 (有平仓动作的K线不再开新仓)
6. `record_orders`: 移除完结订单 → 历史, 落盘
7. `reconcile`: 仓位对账 (节流, 只读告警)

## 关键设计决策

- 账本以"订单"为中心, 每单携带生命周期 phase (见 02-domain-model.md)
- custom_id 全链路复用: 策略生成 order_id → 追单器所有重试沿用 → 交易所挂单。
  提交超时后重试要么命中重复ID被查单接管, 要么挂出同ID订单, 不产生孤儿单
- 平仓单提交后不立即销账: 订单保留在账本 (EXIT_PENDING), 由状态机逐K线确认成交后才移入历史;
  提交结果未知 (超时) 的入场/平仓单以策略生成的已知ID入账, 状态机核实, 查无此单则自然完结/回持仓态
- 对账只读不修复: 漂移原因 (强平/手工平仓等) 需人工决策, 自动修复风险高
- 同一交易对运行多空两个策略实例, 对账必须按仓位方向隔离; 不能用挂单归属判断孤儿单
  (两实例的 clientOrderId 前缀交叉: long_buy 实例的入场单是 sell 前缀, 与 short_sell 实例的平仓单无法区分)
- position_reverse: 配置的 master_side/position_side 是逻辑方向, 实际交易所方向取反

## 遗留观察点 (非缺陷, 运行时留意)

1. Binance 对已撤销订单的 clientOrderId 复用语义按官方文档 ("open orders 内唯一") 设计;
   若交易所行为与文档不符, 追单器会通过"查单接管已撤销单→重试"降级运行, 状态机可自愈
2. 平仓单提交超时后, 状态机查无此单才回持仓态重新触发; 若交易所查询出现罕见的最终一致性延迟,
   理论上存在重复平仓窗口, 由对账告警兜底
