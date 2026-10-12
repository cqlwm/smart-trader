"""v2 订单状态机测试: OrderPhase 生命周期推进、平仓单确认、账本一致性"""

from typing import Any
from unittest.mock import Mock

from pandas import DataFrame

from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import Kline, OrderPhase, OrderSide, PositionSide, Symbol
from smart_trader.strategy import Signal
from smart_trader.strategy.grids_strategy_v2 import (
    Order,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)

TIMEFRAME = "1m"


class _StubSignal(Signal):
    """可控入场/退出信号的桩实现"""

    def __init__(self, side: OrderSide, *, entry: bool = False, exit_: bool = False):
        super().__init__(side)
        self._entry = entry
        self._exit = exit_

    def run(self, klines: DataFrame) -> int:
        return 0

    def is_entry(self, df: DataFrame) -> bool:
        return self._entry

    def is_exit(self, df: DataFrame) -> bool:
        return self._exit


def build_config(**overrides: Any) -> SignalGridStrategyConfig:
    defaults: dict[str, Any] = {
        "symbol": Symbol(base="BTC", quote="USDT"),
        "timeframe": TIMEFRAME,
        "per_order_qty": 100,
        "order_file_path": "",
        "reconcile_interval_sec": 0,  # 状态机测试默认关闭对账
    }
    defaults.update(overrides)
    return SignalGridStrategyConfig(**defaults)


def build_strategy(
    config: SignalGridStrategyConfig,
) -> tuple[SignalGridStrategy, Mock]:
    ex_client = Mock(spec=ExSwapClient)
    return SignalGridStrategy(config, ex_client), ex_client


def set_latest_kline(
    strategy: SignalGridStrategy,
    close: float,
    high: float | None = None,
    low: float | None = None,
) -> None:
    strategy.kline_data_dict[TIMEFRAME].latest_kline = Kline(
        symbol=strategy.config.symbol,
        timeframe=TIMEFRAME,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=0.0,
        timestamp=0,
        finished=True,
    )


def make_order(
    phase: OrderPhase,
    entry_id: str = "buy1",
    price: float = 100.0,
    quantity: float = 100.0,
    exit_id: str | None = None,
    exit_price: float | None = None,
) -> Order:
    status = "open" if phase == OrderPhase.ENTRY_PENDING else "closed"
    return Order(
        entry_id=entry_id,
        side=OrderSide.BUY,
        price=price,
        quantity=quantity,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=status,
        phase=phase,
        exit_id=exit_id,
        exit_price=exit_price,
    )


def query_result(status: str, filled: float = 0.0) -> dict[str, Any]:
    return {"status": status, "filled": filled}


# ------------------------------------------------------------------ 入场单同步


def test_sync_entry_pending_becomes_holding_when_filled():
    """入场挂单成交后转为持仓态"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("closed", filled=100.0)

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.HOLDING, "入场已成交, 应转为持仓态"
    assert order.status == "closed"
    ex_client.query_order.assert_called_once_with("buy1", strategy.config.symbol)


def test_sync_entry_pending_retired_when_canceled_unfilled():
    """入场挂单被撤销且无成交: 从账本完结, 不产生持仓"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("canceled", filled=0.0)

    retired = strategy.sync_order_phases()

    assert retired == [order]
    assert order.phase == OrderPhase.CLOSED, "未成交的入场单完结, 不应计入持仓"


def test_sync_entry_pending_partial_fill_becomes_holding():
    """入场单部分成交后被撤销: 按实际成交量转为持仓"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING, quantity=100.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("canceled", filled=40.0)

    strategy.sync_order_phases()

    assert order.phase == OrderPhase.HOLDING
    assert order.quantity == 40.0, "数量应修正为实际成交量"


def test_sync_entry_pending_missing_order_retires():
    """入场单在交易所不存在: 视为失效完结"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = None

    retired = strategy.sync_order_phases()

    assert retired == [order]
    assert order.status == "expired"


def test_sync_entry_pending_query_error_keeps_phase():
    """入场单查询失败: 保持原状态等待下根K线重试, 不改变账本"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.side_effect = RuntimeError("network")

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.ENTRY_PENDING, "查询失败不应改变订单状态"


# ------------------------------------------------------------------ 平仓单同步


def test_sync_exit_pending_retired_when_exit_filled():
    """平仓单成交: 订单完结, 待移入历史"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("closed", filled=100.0)

    retired = strategy.sync_order_phases()

    assert retired == [order]
    assert order.phase == OrderPhase.CLOSED
    ex_client.query_order.assert_called_once_with("exit1", strategy.config.symbol)


def test_sync_exit_pending_canceled_reverts_to_holding():
    """平仓单被撤销且无成交: 回到持仓态等待重新触发"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("canceled", filled=0.0)

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.HOLDING, "平仓未完成, 应回到持仓态"
    assert order.exit_id is None
    assert order.exit_price is None


def test_sync_exit_pending_partial_fill_reduces_quantity():
    """平仓单部分成交后被撤销: 扣减已成交量后回到持仓态"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(
        OrderPhase.EXIT_PENDING, quantity=100.0, exit_id="exit1", exit_price=101.0
    )
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("canceled", filled=40.0)

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.HOLDING
    assert order.quantity == 60.0, "剩余数量 = 原数量 - 已平仓成交量"


def test_sync_exit_pending_missing_reverts_to_holding():
    """平仓单在交易所不存在: 视为已撤销, 回到持仓态"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = None

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.HOLDING
    assert order.exit_id is None


def test_sync_entry_fill_corrects_price_from_exchange():
    """入场成交确认时以交易所订单价格修正账本价格 (结果未知路径的入场价只是估计值)"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.ENTRY_PENDING, price=100.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = {
        "status": "closed",
        "filled": 100.0,
        "price": 99.5,
    }

    strategy.sync_order_phases()

    assert order.phase == OrderPhase.HOLDING
    assert order.price == 99.5, "应以交易所实际订单价格作为成本价"


def test_sync_exit_fill_corrects_exit_price_from_exchange():
    """平仓成交确认时以交易所订单价格修正平仓价"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = {
        "status": "closed",
        "filled": 100.0,
        "price": 101.2,
    }

    retired = strategy.sync_order_phases()

    assert retired == [order]
    assert order.exit_price == 101.2, "应以交易所实际订单价格作为平仓价"


def test_sync_exit_pending_query_error_keeps_phase():
    """平仓单查询失败: 保持 EXIT_PENDING 等待重试"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.side_effect = RuntimeError("network")

    retired = strategy.sync_order_phases()

    assert retired == []
    assert order.phase == OrderPhase.EXIT_PENDING, "查询失败不应改变订单状态"


# ------------------------------------------------------------------ 平仓触发


def test_exit_submission_keeps_order_in_ledger_as_exit_pending():
    """提交平仓单后订单保留在账本并转为 EXIT_PENDING (提交不等于成交)"""
    config = build_config(fixed_rate_take_profit=True, fixed_take_profit_rate=0.01)
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    order = make_order(OrderPhase.HOLDING)
    strategy.order_manager.add_order(order)
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert result.removed_orders == []
    assert result.exited_orders == [order]
    assert order.phase == OrderPhase.EXIT_PENDING
    assert order.exit_id, "平仓单ID为策略生成并全链路复用 (提交结果未知也可核实)"
    assert order.exit_price == 101.1
    assert (
        order in strategy.order_manager.orders
    ), "平仓在途订单应保留在账本, 由状态机确认成交"


def test_batch_exit_shares_one_exit_order_id():
    """多单触发合并为一笔平仓单, 共享同一 exit_id"""
    config = build_config(
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        close_position_ratio=0.95,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    order1 = make_order(OrderPhase.HOLDING, entry_id="buy1", price=100.0, quantity=100.0)
    order2 = make_order(OrderPhase.HOLDING, entry_id="buy2", price=99.0, quantity=50.0)
    strategy.order_manager.add_order(order1)
    strategy.order_manager.add_order(order2)
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert len(result.exited_orders) == 2
    assert order1.exit_id == order2.exit_id, "批量平仓共享同一平仓单ID"
    assert order1.exit_id, "平仓单ID为策略生成并全链路复用"
    assert ex_client.place_order_v2.call_args[1]["quantity"] == 150.0 * 0.95


def test_exit_submission_unknown_marks_exit_pending_for_verification():
    """平仓单提交结果未知(超时): 订单转入 EXIT_PENDING 并记录已知ID, 状态机查无此单则回到持仓态"""
    config = build_config(fixed_rate_take_profit=True, fixed_take_profit_rate=0.01)
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.side_effect = RuntimeError("timeout")
    order = make_order(OrderPhase.HOLDING)
    strategy.order_manager.add_order(order)
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert result.exited_orders == [order]
    assert order.phase == OrderPhase.EXIT_PENDING
    assert order.exit_id, "应记录策略生成的平仓单ID待状态机核实"

    # 状态机核实: 交易所查无此单 → 回到持仓态等待重新触发
    ex_client.query_order.return_value = None
    strategy.sync_order_phases()
    assert order.phase == OrderPhase.HOLDING
    assert order.exit_id is None


def test_exit_signal_triggers_exit_at_min_profit():
    """信号退出: 盈利达到最小止盈率且出现退出信号时平仓"""
    config = build_config(
        enable_exit_signal=True,
        signal=_StubSignal(OrderSide.BUY, exit_=True),
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 100.3}
    order = make_order(OrderPhase.HOLDING)
    strategy.order_manager.add_order(order)
    set_latest_kline(strategy, 100.3)  # 盈利0.3% > signal_min 0.2%, 未达固定止盈1%

    result = strategy.check_close_order()

    assert result.exited_orders == [order]
    assert order.phase == OrderPhase.EXIT_PENDING


def test_max_order_stop_all_exits_every_layer_and_pauses():
    """达到最大订单数触发全平: 所有持仓层提交平仓并暂停策略"""
    config = build_config(
        enable_max_order_stop_loss=True,
        max_order=3,
        paused_after_stop_loss=True,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 100.0}
    strategy.order_manager.add_order(make_order(OrderPhase.HOLDING, entry_id="a"))
    strategy.order_manager.add_order(
        make_order(OrderPhase.HOLDING, entry_id="b", price=101.0)
    )
    set_latest_kline(strategy, 100.0)
    stop_loss_called: list[bool] = []
    strategy.on_stop_loss_order_all = lambda: stop_loss_called.append(True)

    result = strategy.check_close_order()

    assert len(result.exited_orders) == 2, "全平时所有持仓层都应提交平仓"
    assert stop_loss_called, "应触发全平回调"
    assert strategy.is_running is False, "止损后应暂停策略"


def test_close_trigger_cancels_unfilled_entry_and_retires():
    """触发平仓条件时入场单仍未成交: 撤销挂单并从账本移除, 不下平仓单"""
    config = build_config(fixed_rate_take_profit=True, fixed_take_profit_rate=0.01)
    strategy, ex_client = build_strategy(config)
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("open")
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert result.removed_orders == [order]
    assert result.exited_orders == []
    assert order.phase == OrderPhase.CLOSED
    ex_client.cancel.assert_called_once_with("buy1", strategy.config.symbol)
    ex_client.place_order_v2.assert_not_called()


def test_close_trigger_entry_filled_in_flight_exits_position():
    """触发平仓时发现入场单刚成交: 转持仓并立即平仓"""
    config = build_config(fixed_rate_take_profit=True, fixed_take_profit_rate=0.01)
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    order = make_order(OrderPhase.ENTRY_PENDING)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("closed", filled=100.0)
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert result.removed_orders == []
    assert result.exited_orders == [order]
    assert order.phase == OrderPhase.EXIT_PENDING
    ex_client.cancel.assert_not_called()


def test_close_check_skips_exit_pending_and_closed_orders():
    """平仓在途与已完结的订单不再参与平仓检查"""
    config = build_config(fixed_rate_take_profit=True, fixed_take_profit_rate=0.01)
    strategy, ex_client = build_strategy(config)
    exiting = make_order(
        OrderPhase.EXIT_PENDING, entry_id="buy1", exit_id="exit1", exit_price=101.0
    )
    closed = make_order(OrderPhase.CLOSED, entry_id="buy2")
    strategy.order_manager.add_order(exiting)
    strategy.order_manager.add_order(closed)
    set_latest_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert result.removed_orders == []
    assert result.exited_orders == []
    ex_client.query_order.assert_not_called()
    ex_client.place_order_v2.assert_not_called()


# ------------------------------------------------------------------ 账本一致性


def test_record_orders_deduplicates_closed_orders():
    """同一订单重复进入完结列表时, 历史只记录一次"""
    strategy, _ = build_strategy(build_config())
    order = make_order(OrderPhase.CLOSED)
    strategy.order_manager.add_order(order)

    strategy.order_manager.record_orders([order, order])

    assert strategy.order_manager.orders == []
    assert len(strategy.order_manager.history_orders) == 1, "重复的完结订单不应重复进入历史"


def test_sync_with_no_orders_queries_nothing():
    """空账本同步不做任何交易所查询"""
    strategy, ex_client = build_strategy(build_config())

    assert strategy.sync_order_phases() == []
    ex_client.query_order.assert_not_called()


def test_kline_finished_confirms_exit_and_records_history():
    """完整K线流程: 平仓单成交 → 订单移入历史"""
    strategy, ex_client = build_strategy(build_config())
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("closed", filled=100.0)
    set_latest_kline(strategy, 101.1)

    strategy._on_kline_finished()

    assert strategy.order_manager.orders == []
    assert strategy.order_manager.history_orders == [order]
    assert order.phase == OrderPhase.CLOSED
    ex_client.place_order_v2.assert_not_called()


def test_paused_strategy_still_confirms_pending_exits():
    """策略暂停后状态机仍然运行: 确认在途平仓单, 不开新仓不平新仓"""
    strategy, ex_client = build_strategy(build_config())
    strategy.is_running = False
    order = make_order(OrderPhase.EXIT_PENDING, exit_id="exit1", exit_price=101.0)
    strategy.order_manager.add_order(order)
    ex_client.query_order.return_value = query_result("closed", filled=100.0)
    set_latest_kline(strategy, 101.1)

    strategy._on_kline_finished()

    assert strategy.order_manager.orders == []
    assert strategy.order_manager.history_orders == [order]
    assert not ex_client.place_order_v2.called, "暂停状态下不应有任何新下单"


# ------------------------------------------------------------------ 旧文件迁移


def test_legacy_order_without_phase_infers_lifecycle():
    """旧版本账本无 phase 字段: 按已有字段推断生命周期"""
    base: dict[str, Any] = {
        "entry_id": "buy1",
        "side": "buy",
        "price": 100.0,
        "quantity": 100.0,
        "fixed_take_profit_rate": 0.01,
        "signal_min_take_profit_rate": 0.002,
    }

    pending = Order.model_validate({**base, "status": "open"})
    holding = Order.model_validate({**base, "status": "closed"})
    exiting = Order.model_validate(
        {**base, "status": "closed", "exit_id": "exit1", "exit_price": 101.0}
    )

    assert pending.phase == OrderPhase.ENTRY_PENDING
    assert holding.phase == OrderPhase.HOLDING
    assert exiting.phase == OrderPhase.EXIT_PENDING


# ------------------------------------------------------------------ 实时限价止盈


def test_limit_take_profit_marks_exit_pending_on_submission():
    """实时限价止盈: 入场成交后挂出止盈单, 订单转 EXIT_PENDING"""
    config = build_config(
        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.01,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.query_order.return_value = {"status": "closed", "filled": 100.0}
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "tp1",
        "price": 101.0,
        "status": "open",
    }
    order = make_order(OrderPhase.ENTRY_PENDING, price=100.0)
    strategy.order_manager.add_order(order)
    set_latest_kline(strategy, 100.5)

    strategy._on_kline()

    assert order.phase == OrderPhase.EXIT_PENDING
    assert order.exit_id == "tp1"
    assert order.exit_price == 101.0


def test_limit_take_profit_unknown_submission_marks_exit_pending():
    """止盈单提交结果未知: 记录策略生成的ID转 EXIT_PENDING, 状态机核实"""
    config = build_config(
        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
        fixed_take_profit_rate=0.01,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.query_order.return_value = {"status": "closed", "filled": 100.0}
    ex_client.place_order_v2.side_effect = RuntimeError("timeout")
    order = make_order(OrderPhase.ENTRY_PENDING, price=100.0)
    strategy.order_manager.add_order(order)
    set_latest_kline(strategy, 100.5)

    strategy._on_kline()

    assert order.phase == OrderPhase.EXIT_PENDING
    assert order.exit_id, "应记录策略生成的止盈单ID待状态机核实"


# ------------------------------------------------------------------ 开仓与对账装配


def test_open_order_submission_unknown_keeps_order_for_verification():
    """入场单提交结果未知(超时): 以策略生成的ID入账为 ENTRY_PENDING, 状态机核实; 查无此单自然完结"""
    strategy, ex_client = build_strategy(build_config())
    ex_client.place_order_v2.side_effect = RuntimeError("timeout")
    set_latest_kline(strategy, 100.0)

    assert strategy.check_open_order() is True
    orders = strategy.order_manager.orders
    assert len(orders) == 1, "结果未知的订单应入账, 交由状态机核实"
    assert orders[0].phase == OrderPhase.ENTRY_PENDING

    # 状态机核实: 交易所查无此单 → 自然完结, 经 record_orders 移出账本
    ex_client.query_order.return_value = None
    retired = strategy.sync_order_phases()
    assert len(retired) == 1
    assert retired[0].phase == OrderPhase.CLOSED
    strategy.order_manager.record_orders(retired)
    assert strategy.order_manager.orders == []


def test_grid_spacing_anchor_ignores_exit_pending_orders():
    """平仓在途的订单不作为网格间距锚点 (与旧版"平仓即移除"行为一致)"""
    config = build_config(grid_spacing_rate=0.01)
    strategy, ex_client = build_strategy(config)
    ex_client.query_order.return_value = query_result("open")
    exiting = make_order(
        OrderPhase.EXIT_PENDING,
        entry_id="buy1",
        price=100.0,
        exit_id="exit1",
        exit_price=101.0,
    )
    holding = make_order(OrderPhase.HOLDING, entry_id="buy2", price=110.0)
    strategy.order_manager.add_order(exiting)
    strategy.order_manager.add_order(holding)
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "buy3",
        "price": 108.9,
        "status": "open",
    }
    set_latest_kline(strategy, 108.9)  # 相对 110 跌幅 1%, 满足间距

    assert strategy.check_open_order() is True
    assert len(strategy.order_manager.orders) == 3


def test_expected_position_qty_counts_holding_and_exit_pending_only():
    """预期持仓 = 持仓中 + 平仓在途 (平仓成交前仓位仍然存在)"""
    strategy, _ = build_strategy(build_config())
    strategy.order_manager.add_order(
        make_order(OrderPhase.ENTRY_PENDING, entry_id="p1", quantity=250.0)
    )
    strategy.order_manager.add_order(
        make_order(OrderPhase.HOLDING, entry_id="h1", quantity=250.0)
    )
    strategy.order_manager.add_order(
        make_order(
            OrderPhase.EXIT_PENDING,
            entry_id="e1",
            quantity=250.0,
            exit_id="x1",
            exit_price=101.0,
        )
    )
    strategy.order_manager.add_order(
        make_order(OrderPhase.CLOSED, entry_id="c1", quantity=250.0)
    )

    assert strategy._expected_position_qty() == 500.0


def test_reconciler_disabled_when_interval_zero():
    """reconcile_interval_sec <= 0 时不装配对账组件"""
    strategy, _ = build_strategy(build_config(reconcile_interval_sec=0))

    assert strategy.reconciler is None


def test_reconciler_watches_reversed_position_side():
    """反转持仓策略的对账方向取反"""
    strategy, _ = build_strategy(
        build_config(position_reverse=True, reconcile_interval_sec=60.0)
    )

    assert strategy.reconciler is not None
    assert strategy.reconciler.position_side == PositionSide.SHORT


def test_kline_finished_runs_throttled_reconcile():
    """对账按间隔节流: 间隔内多根K线只对账一次"""
    strategy, ex_client = build_strategy(build_config(reconcile_interval_sec=60.0))
    ex_client.place_order_v2.return_value = None
    ex_client.query_order.return_value = query_result("open")
    ex_client.positions.return_value = []
    set_latest_kline(strategy, 100.0)

    strategy._on_kline_finished()
    strategy._on_kline_finished()

    assert ex_client.positions.call_count == 1, "间隔内的第二根K线不应重复对账"
    ex_client.positions.assert_called_once_with("BTC/USDT")
