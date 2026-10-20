"""持仓对账器测试：以交易所实际持仓为准收敛本地缺口

覆盖：数量一致无动作、全量外部平仓内部归档（先撤挂单）、部分缺口 LIFO 分摊、
反向偏差仅告警、查询/撤单异常容错、多空两方向的"最外层优先"顺序、
告警去重（差额变化才再次告警）。
用 tests.fakes 的内存端口 + :memory: 账本，不触网、不落盘。
"""

import logging

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import (
    OrderResult,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import OrderRecord
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.position_lifecycle import PositionLifecycle
from smart_trader.strategy.position_reconciler import (
    PositionReconciler,
    PositionReconcileResult,
)
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeAccountPort,
    FakeTradingPort,
    make_exchange_position,
    make_order_record,
    make_position,
)

LONG_FIELDS: dict[str, object] = {
    "symbol": DEFAULT_SYMBOL,
    "timeframe": "1m",
    "strategy_key": DEFAULT_STRATEGY_KEY,
    "position_side": PositionSide.LONG,
    "place_order_behavior": PlaceOrderBehavior.NORMAL,
}
SHORT_FIELDS: dict[str, object] = {
    "position_side": PositionSide.SHORT,
    "master_side": OrderSide.SELL,
}


class ReconcileHarness:
    """对账器测试夹具：内存端口 + 账本 + 可编程交易所持仓"""

    def __init__(
        self,
        positions: list[GridPosition],
        *,
        config_fields: dict[str, object] | None = None,
    ) -> None:
        fields = dict(LONG_FIELDS)
        fields.update(config_fields or {})
        self.config = SignalGridStrategyConfig.model_validate(fields)
        self.account = FakeAccountPort()
        self.trading = FakeTradingPort(status=OrderStatus.CANCELED)
        self.journal = OrderJournal(":memory:")
        self.positions = positions
        self.lifecycle = PositionLifecycle(
            config=self.config,
            trading=self.trading,
            journal=self.journal,
            positions=lambda: self.positions,
        )
        self.reconciler = PositionReconciler(
            config=self.config,
            account=self.account,
            positions=lambda: self.positions,
            lifecycle=self.lifecycle,
        )

    def set_exchange_position(
        self, quantity: float, *, side: PositionSide = PositionSide.LONG
    ) -> None:
        self.account.set_positions(make_exchange_position(quantity, side=side))


def make_holding_position(
    position_id: str,
    quantity: float,
    entry_price: float,
    *,
    orders: list[OrderRecord] | None = None,
    config_fields: dict[str, object] | None = None,
) -> GridPosition:
    """构造持仓中的逻辑仓位：一条按 quantity 成交的入场单"""
    fields = dict(LONG_FIELDS)
    fields.update(config_fields or {})
    side = fields["position_side"]
    assert isinstance(side, PositionSide)
    entry_side = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL
    return make_position(
        position_id,
        orders=orders
        if orders is not None
        else [
            make_order_record(
                f"o-{position_id}",
                position_id,
                side=entry_side,
                price=entry_price,
                quantity=quantity,
                filled_qty=quantity,
                avg_price=entry_price,
                status=OrderStatus.CLOSED,
            )
        ],
        main_order_side=entry_side,
        position_side=side,
    )


def orders_with_pending_exit(
    position_id: str, quantity: float, entry_price: float, exit_price: float = 0.6
) -> list[OrderRecord]:
    """入场已成交 + 一张 OPEN 出场挂单（多头仓位）"""
    return [
        make_order_record(
            f"o-{position_id}",
            position_id,
            side=OrderSide.BUY,
            price=entry_price,
            quantity=quantity,
            filled_qty=quantity,
            avg_price=entry_price,
            status=OrderStatus.CLOSED,
        ),
        make_order_record(
            f"o-{position_id}-exit",
            position_id,
            side=OrderSide.SELL,
            price=exit_price,
            quantity=quantity,
            status=OrderStatus.OPEN,
        ),
    ]


# ------------------------------------------------------------------ 基础行为


def test_no_positions_skips_exchange_query() -> None:
    harness = ReconcileHarness([])
    result = harness.reconciler.reconcile()
    assert result.archived == [], "无仓位时不应有归档"
    assert result.adjustments == {}, "无仓位时不应有扣减"
    assert harness.account.positions_calls == 0, "无仓位时不应查询交易所持仓"


def test_matching_quantity_no_action() -> None:
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(100.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [], f"数量一致不应归档, 实际 {result.archived}"
    assert result.adjustments == {}, f"数量一致不应有扣减, 实际 {result.adjustments}"


def test_float_noise_treated_as_consistent() -> None:
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(100.0 - 1e-9)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "浮点噪声级差异不应触发归档"
    assert result.adjustments == {}, "浮点噪声级差异不应触发扣减"


def test_exchange_surplus_only_warns_without_change() -> None:
    """交易所多于本地（来源不明仓位）: 不自动收编, 原样返回"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(150.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "反向偏差不应归档仓位"
    assert result.adjustments == {}, "反向偏差不应产生扣减"


def test_opposite_side_exchange_position_ignored() -> None:
    """对冲模式另一方向持仓不计入对账基数"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.account.set_positions(make_exchange_position(50.0, side=PositionSide.SHORT))
    result = harness.reconciler.reconcile()
    assert result.archived == [position], "反方向持仓不抵消本地多头, 缺口应全量归档"


# ------------------------------------------------------------------ 全量外部平仓


def test_full_external_close_archives_position() -> None:
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(0.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [position], "交易所零持仓时应内部归档仓位"
    assert result.adjustments == {}, "归档仓位不进入扣减表"


def test_internal_close_cancels_pending_exit_order() -> None:
    position = make_holding_position(
        "POS1", 100.0, 0.5, orders=orders_with_pending_exit("POS1", 100.0, 0.5)
    )
    harness = ReconcileHarness([position])
    harness.set_exchange_position(0.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [position]
    assert position.pending_exit_order is None, "内部归档前应撤掉出场挂单"
    assert harness.trading.canceled == ["o-POS1-exit"], (
        f"应向交易所撤出场单, 实际撤单 {harness.trading.canceled}"
    )


def test_internal_close_cancels_pending_entry_orders() -> None:
    """归档前撤掉未成交入场单, 防止继续建仓"""
    filled = make_order_record(
        "o-POS1-1",
        "POS1",
        side=OrderSide.BUY,
        quantity=100.0,
        filled_qty=100.0,
        avg_price=0.5,
        status=OrderStatus.CLOSED,
    )
    pending = make_order_record(
        "o-POS1-2",
        "POS1",
        side=OrderSide.BUY,
        quantity=100.0,
        status=OrderStatus.OPEN,
    )
    position = make_position("POS1", orders=[filled, pending])
    harness = ReconcileHarness([position])
    harness.set_exchange_position(0.0)
    # 交易所事实: 入场单仍 OPEN, 撤单复查后为 CANCELED 终态
    harness.trading.queue_query(
        OrderResult(
            custom_id="o-POS1-2",
            symbol=DEFAULT_SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=100.0,
        ),
        OrderResult(
            custom_id="o-POS1-2",
            symbol=DEFAULT_SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.CANCELED,
            quantity=100.0,
        ),
    )
    result = harness.reconciler.reconcile()
    assert result.archived == [position]
    assert harness.trading.canceled == ["o-POS1-2"], (
        f"内部归档前应撤掉未成交入场单, 实际撤单 {harness.trading.canceled}"
    )


def test_zero_position_without_orders_ignored_by_reconcile() -> None:
    """quantity=0 的空仓位（无持仓语义）不参与缺口, 也不被对账误归档"""
    position = make_holding_position("POS1", 0.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(0.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "零数量空仓位应交给孤儿回收, 不归对账管"


# ------------------------------------------------------------------ 部分缺口 LIFO 分摊


def test_partial_external_close_records_adjustment() -> None:
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(50.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "仍有剩余持仓的仓位不应归档"
    assert result.adjustments == {"POS1": 50.0}, (
        f"应记录外部扣减量 50, 实际 {result.adjustments}"
    )


def test_gap_consumed_by_outermost_layer_first() -> None:
    """多头最外层 = 盈利方向极值层(最高价): 缺口优先扣它, 内层不动"""
    outer = make_holding_position("POS-OUTER", 60.0, 0.60)
    inner = make_holding_position("POS-INNER", 40.0, 0.40)
    harness = ReconcileHarness([inner, outer])
    harness.set_exchange_position(50.0)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "缺口未吃满任何一层时不应归档"
    assert result.adjustments == {"POS-OUTER": 50.0}, (
        f"缺口应由最高价层(最外层)承担, 实际 {result.adjustments}"
    )


def test_gap_spills_to_next_layer_after_full_consume() -> None:
    outer = make_holding_position("POS-OUTER", 60.0, 0.60)
    inner = make_holding_position("POS-INNER", 40.0, 0.40)
    harness = ReconcileHarness([inner, outer])
    # 缺口 70: 外层 60 全吃归档, 剩余 10 溢出到次外层
    harness.set_exchange_position(30.0)
    result = harness.reconciler.reconcile()
    assert [item.id for item in result.archived] == ["POS-OUTER"], "最外层被全吃应归档"
    assert result.adjustments == {"POS-INNER": 10.0}, (
        f"剩余缺口应溢出到次外层, 实际 {result.adjustments}"
    )


def test_short_direction_outermost_is_lowest_price() -> None:
    """空头最外层 = 盈利方向极值层(最低价): 缺口优先扣它"""
    outer = make_holding_position("POS-OUTER", 60.0, 0.40, config_fields=SHORT_FIELDS)
    inner = make_holding_position("POS-INNER", 40.0, 0.80, config_fields=SHORT_FIELDS)
    harness = ReconcileHarness([inner, outer], config_fields=SHORT_FIELDS)
    harness.set_exchange_position(50.0, side=PositionSide.SHORT)
    result = harness.reconciler.reconcile()
    assert result.archived == [], "缺口未吃满任何一层时不应归档"
    assert result.adjustments == {"POS-OUTER": 50.0}, (
        f"空头缺口应由最低价层(最外层)承担, 实际 {result.adjustments}"
    )


# ------------------------------------------------------------------ 容错语义


def test_account_failure_skips_round() -> None:
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.account.fail_positions(
        SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "查询持仓失败")
    )
    result = harness.reconciler.reconcile()
    assert result.archived == [], "查询异常应跳过本轮而非崩溃"
    assert result.adjustments == {}, "查询异常本轮不应产出扣减"


def test_cancel_failure_keeps_position_for_retry() -> None:
    """归档前撤单失败: 该仓位本轮不归档, 下一根 K 线重试"""
    position = make_holding_position(
        "POS1", 100.0, 0.5, orders=orders_with_pending_exit("POS1", 100.0, 0.5)
    )
    harness = ReconcileHarness([position])
    harness.set_exchange_position(0.0)
    harness.trading.queue_cancel(
        SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "撤单失败")
    )
    result = harness.reconciler.reconcile()
    assert result.archived == [], "撤单失败应保留仓位待下一轮重试"
    assert result.adjustments == {}, "撤单失败不应产出扣减"


def test_result_is_fresh_object_each_round() -> None:
    """每轮对账返回全新结果对象, 不携带上一轮状态"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(50.0)
    first = harness.reconciler.reconcile()
    assert first.adjustments == {"POS1": 50.0}
    harness.set_exchange_position(100.0)
    second = harness.reconciler.reconcile()
    assert isinstance(second, PositionReconcileResult)
    assert second.adjustments == {}, f"第二轮应重新计算, 实际 {second.adjustments}"
    assert second.archived == []


# ------------------------------------------------------------------ 告警去重（差额变化才再次告警）


def test_surplus_warning_not_repeated_while_gap_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """反向偏差告警: 差额不变只告警一次, 不随K线重复刷屏"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(150.0)
    with caplog.at_level(
        logging.WARNING, logger="smart_trader.strategy.position_reconciler"
    ):
        harness.reconciler.reconcile()
        harness.reconciler.reconcile()
        harness.reconciler.reconcile()

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1, f"差额不变只应告警一次, 实际 {len(warns)} 条"


def test_surplus_warning_repeats_after_gap_changes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """差额变化(150→160)后应再次告警"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    with caplog.at_level(
        logging.WARNING, logger="smart_trader.strategy.position_reconciler"
    ):
        harness.set_exchange_position(150.0)
        harness.reconciler.reconcile()
        harness.reconciler.reconcile()
        harness.set_exchange_position(160.0)
        harness.reconciler.reconcile()

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 2, f"差额变化应再次告警, 实际 {len(warns)} 条"


def test_recovery_info_logged_when_gap_returns_to_consistent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """差额归零(恢复一致)时给一条收敛提示"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    with caplog.at_level(
        logging.INFO, logger="smart_trader.strategy.position_reconciler"
    ):
        harness.set_exchange_position(150.0)
        harness.reconciler.reconcile()
        harness.set_exchange_position(100.0)
        harness.reconciler.reconcile()

    infos = [
        r for r in caplog.records if r.levelno == logging.INFO and "已收敛" in r.message
    ]
    assert len(infos) == 1, f"差额归零应给一条收敛提示, 实际 {len(infos)} 条"


def test_first_consistent_round_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """启动即一致(无历史告警)时不打收敛提示"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(100.0)
    with caplog.at_level(
        logging.INFO, logger="smart_trader.strategy.position_reconciler"
    ):
        harness.reconciler.reconcile()

    msgs = [r.message for r in caplog.records if "对账" in r.message]
    assert msgs == [], f"首轮一致不应有任何对账日志, 实际 {msgs}"


def test_deficit_warning_not_repeated_while_gap_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """正向缺口(本地多于交易所)告警同样按差额变化去重（含分摊明细）"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(50.0)
    with caplog.at_level(
        logging.WARNING, logger="smart_trader.strategy.position_reconciler"
    ):
        harness.reconciler.reconcile()
        first_round = len([r for r in caplog.records if r.levelno == logging.WARNING])
        harness.reconciler.reconcile()
        harness.reconciler.reconcile()
        total = len([r for r in caplog.records if r.levelno == logging.WARNING])

    assert first_round >= 1, "首轮必须告警"
    assert total == first_round, (
        f"差额不变后续轮不得新增告警: 首轮 {first_round} 条, 累计 {total} 条"
    )


def test_adjustments_still_returned_on_silent_rounds() -> None:
    """静默轮(不重复告警)不影响对账结果: 扣减量照常产出, clamp 不受影响"""
    position = make_holding_position("POS1", 100.0, 0.5)
    harness = ReconcileHarness([position])
    harness.set_exchange_position(50.0)

    first = harness.reconciler.reconcile()
    second = harness.reconciler.reconcile()

    assert first.adjustments == {"POS1": 50.0}
    assert second.adjustments == {"POS1": 50.0}, (
        f"静默轮仍必须产出扣减量, 实际 {second.adjustments}"
    )


def test_lifo_gap_allocation_prefers_outermost_layer() -> None:
    """多头对账分摊顺序: 最外层（最高价, 盈利方向极值）优先"""
    cheapest = make_holding_position("POS-CHEAP", 10.0, 0.30)
    middle = make_holding_position("POS-MID", 10.0, 0.50)
    dearest = make_holding_position("POS-DEAR", 10.0, 0.70)
    positions = [cheapest, middle, dearest]

    harness = ReconcileHarness(positions)
    # 缺口 25: 最高价层 10 全吃归档 → 次层 10 全吃归档 → 剩 5 记到最低价层
    harness.set_exchange_position(5.0)
    result = harness.reconciler.reconcile()

    assert [item.id for item in result.archived] == ["POS-DEAR", "POS-MID"]
    assert result.adjustments == {"POS-CHEAP": 5.0}, (
        f"对账分摊顺序应为最高价优先, 实际 {result.adjustments}"
    )
