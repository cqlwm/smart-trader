"""SignalGridStrategy 装配契约与边界行为测试

覆盖重构引入的装配语义（`SignalKind` → 闸门类型）与必须保留的现状边界行为。
闸门/策略自身的规则由 `test_entry_gate.py` / `test_exit_policy.py` 覆盖，
本文件只验证**策略如何装配它们**以及**端到端行为**。
"""

from typing import TypeVar

from smart_trader.model import OrderSide, PositionSide, SignalDirection, SignalKind
from smart_trader.strategy.entry_gate import (
    DirectionalSignalGate,
    EntryGate,
    SignalTriggerGate,
)
from smart_trader.strategy.exit_policy import (
    FixedTakeProfitPolicy,
    RestingTakeProfitPolicy,
)
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from tests.fakes import FakeSignal
from tests.test_signal_grid_strategy import build_strategy, push_kline, seed_position

GateT = TypeVar("GateT", bound=EntryGate)

# ---- 装配契约: SignalKind 决定闸门类型 ----


def test_trigger_kind_assembles_signal_trigger_gate():
    """TRIGGER 装配无方向倾向闸门"""
    strategy, _, _ = build_strategy(signal_kind=SignalKind.TRIGGER)

    gate = _entry_gate_of(strategy, SignalTriggerGate)

    assert gate is not None, "TRIGGER 应装配 SignalTriggerGate"
    assert _entry_gate_of(strategy, DirectionalSignalGate) is None, (
        "TRIGGER 不应同时装配方向性闸门"
    )


def test_directional_kind_assembles_directional_signal_gate():
    """DIRECTIONAL 装配方向性闸门"""
    strategy, _, _ = build_strategy(
        signal=FakeSignal(), signal_kind=SignalKind.DIRECTIONAL
    )

    gate = _entry_gate_of(strategy, DirectionalSignalGate)

    assert gate is not None, "DIRECTIONAL 应装配 DirectionalSignalGate"
    assert _entry_gate_of(strategy, SignalTriggerGate) is None, (
        "DIRECTIONAL 不应同时装配触发型闸门"
    )


def test_directional_gate_uses_configured_master_side():
    """方向性闸门使用 config.master_side 作为入场方向"""
    strategy, _, _ = build_strategy(
        master_side=OrderSide.SELL,
        signal=FakeSignal(),
        signal_kind=SignalKind.DIRECTIONAL,
    )

    gate = _entry_gate_of(strategy, DirectionalSignalGate)

    assert gate is not None
    assert gate.entry_side is OrderSide.SELL, (
        "空头网格的方向闸门应以 SELL 为期望入场方向"
    )


def test_default_signal_kind_is_directional():
    """默认信号种类为 DIRECTIONAL（与旧 Signal(side=...) 的方向语义一致）"""
    strategy, _, _ = build_strategy()

    assert strategy.config.signal_kind is SignalKind.DIRECTIONAL, (
        "默认值应保持既有方向语义"
    )


def test_fixed_take_profit_disabled_omits_policy():
    """未启用固定止盈时不装配 FixedTakeProfitPolicy"""
    strategy, _, _ = build_strategy(fixed_rate_take_profit=False)

    assert not _has_fixed_take_profit_policy(strategy), (
        "关闭固定止盈时不应把该策略放进链里"
    )


def test_fixed_take_profit_enabled_adds_policy():
    """启用固定止盈时装配 FixedTakeProfitPolicy"""
    strategy, _, _ = build_strategy(fixed_rate_take_profit=True)

    assert _has_fixed_take_profit_policy(strategy), (
        "启用固定止盈时必须装配该策略"
    )


def test_resting_take_profit_only_when_limit_order_enabled():
    """仅在同时开启固定止盈与限价止盈时装配休息限价止盈"""
    strategy, _, _ = build_strategy(
        fixed_rate_take_profit=True, take_profit_use_limit_order=True
    )

    assert isinstance(strategy._resting_take_profit, RestingTakeProfitPolicy), (
        "两者同时开启时应装配 RestingTakeProfitPolicy"
    )


def test_resting_take_profit_absent_when_limit_order_disabled():
    """未开启限价止盈时不装配（盘中止盈路径整体跳过）"""
    strategy, _, _ = build_strategy(
        fixed_rate_take_profit=True, take_profit_use_limit_order=False
    )

    assert strategy._resting_take_profit is None, (
        "未开启限价止盈时不应装配"
    )


def test_entry_gate_chain_preserves_declared_order():
    """闸门链顺序: 上限 → 信号 → 价格区间 → 间距 → 上限止损"""
    strategy, _, _ = build_strategy(
        signal=FakeSignal(), signal_kind=SignalKind.TRIGGER, lowest_price=0.4
    )

    names = [type(gate).__name__ for gate in strategy.entry_gates.gates]

    assert names == [
        "MaxPositionsGate",
        "SignalTriggerGate",
        "PriceRangeGate",
        "GridSpacingGate",
        "MaxOrderStopLossGate",
    ], "闸门顺序必须与既有判定顺序一致"


# ---- 边界行为（必须保留的现状语义）----


def test_first_position_never_opens_when_max_order_is_one():
    """反直觉现状: max_order=1 且空仓时上限止损条件成立, 永不下第一单"""
    strategy, trading, _ = build_strategy(
        max_order=1, enable_max_order_stop_loss=True
    )

    push_kline(strategy, 0.5)

    assert strategy.positions == [], "max_order=1 时现状永不开第一单"
    assert trading.placed == [], "不应产生任何委托"


def test_opens_first_position_when_max_order_is_two():
    """max_order=2 空仓时还有 2 个空位, 应正常开仓"""
    strategy, _, _ = build_strategy(max_order=2, enable_max_order_stop_loss=True)

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1, "还有 2 个空位时应正常开仓"


def test_manual_close_position_does_not_trigger_stop_loss_callback():
    """手动全平不触发止损回调（本次修复的缺陷）"""
    strategy, _, _ = build_strategy()
    seed_position(strategy, "POS1", entry_price=0.5, quantity=100.0)
    calls: list[str] = []
    strategy.on_stop_loss_order_all = lambda: calls.append("called")
    strategy.close_position = True

    push_kline(strategy, 0.5)

    assert calls == [], "手动全平是用户主动行为, 不应触发止损回调"
    assert strategy.is_running is True, "手动全平不应暂停策略"


def test_manual_close_position_still_exits_all_positions():
    """手动全平仍然离场（修复未改变离场行为）"""
    strategy, trading, _ = build_strategy()
    seed_position(strategy, "POS1", entry_price=0.5, quantity=100.0)
    strategy.close_position = True

    push_kline(strategy, 0.5)

    assert [request.side for request in trading.placed] == [OrderSide.SELL], (
        "手动全平应挂出出场单"
    )


def test_manual_close_position_resets_flag():
    """手动全平标志消费后复位（幂等）"""
    strategy, _, _ = build_strategy()
    seed_position(strategy, "POS1", entry_price=0.5, quantity=100.0)
    strategy.close_position = True

    push_kline(strategy, 0.5)

    assert strategy.close_position is False, "手动全平标志消费后必须复位"


def test_stop_all_at_max_order_still_triggers_callback_and_pause():
    """达到上限止损仍触发回调并暂停（修复未改变止损行为）"""
    strategy, _, _ = build_strategy(
        max_order=2, enable_max_order_stop_loss=True, paused_after_stop_loss=True
    )
    seed_position(strategy, "POS1", entry_price=0.5, quantity=100.0)
    calls: list[str] = []
    strategy.on_stop_loss_order_all = lambda: calls.append("called")

    push_kline(strategy, 0.5)

    assert calls == ["called"], "达到上限止损应触发回调"
    assert strategy.is_running is False, "达到上限止损应暂停策略"


def test_stop_all_without_pause_still_exits_but_keeps_running():
    """不暂停时: 仍全部离场, 但策略保持运行"""
    strategy, trading, _ = build_strategy(
        max_order=2,
        enable_max_order_stop_loss=True,
        paused_after_stop_loss=False,
    )
    seed_position(strategy, "POS1", entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.5)

    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert strategy.is_running is True, "未开启暂停时策略应继续运行"


def test_no_exit_order_when_no_position_qualifies():
    """无仓位满足离场条件时不挂单"""
    strategy, trading, _ = build_strategy(signal=FakeSignal([SignalDirection.LONG]))
    seed_position(
        strategy,
        "POS1",
        entry_price=0.5,
        quantity=100.0,
        fixed_take_profit_rate=0.1,
        signal_min_take_profit_rate=0.1,
    )

    push_kline(strategy, 0.505)

    assert trading.placed == [], "盈利未达任何止盈阈值时不应挂单"


def test_short_position_exit_side_is_buy():
    """空头仓位的出场方向是买入（价格下跌触发止盈）

    注意: 空头盈利方向与多头相反 —— `profit_level` 对 SHORT 用 `<` 比较
    `price × (1 - rate)`, 因此 close < 入场价才是盈利。
    """
    strategy, trading, _ = build_strategy(
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        fixed_rate_take_profit=True,
    )
    seed_position(
        strategy,
        "POS1",
        position_side=PositionSide.SHORT,
        main_order_side=OrderSide.SELL,
        entry_price=0.5,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
    )

    # 空头入场价 0.5, 跌到 0.49 即跌破 0.5 × (1 - 1%) -> 以买入平仓
    push_kline(strategy, 0.49)

    assert [request.side for request in trading.placed] == [OrderSide.BUY], (
        "空头仓位价格下跌触发止盈, 应以买入平仓"
    )


# ---- helpers ----


def _entry_gate_of(
    strategy: SignalGridStrategy, gate_type: type[GateT]
) -> GateT | None:
    """从策略已装配的闸门链里找指定类型的闸门"""
    return next(
        (gate for gate in strategy.entry_gates.gates if isinstance(gate, gate_type)),
        None,
    )


def _has_fixed_take_profit_policy(strategy: SignalGridStrategy) -> bool:
    return any(
        isinstance(policy, FixedTakeProfitPolicy)
        for policy in strategy.exit_policies.policies
    )