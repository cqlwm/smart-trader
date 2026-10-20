"""离场策略链测试

覆盖: 各策略规则 / force 语义 / 合并顺序无关性 / 隔离 / 空值与边界。
判定口径（与既有实现逐字一致）：
- `StopLossPolicy` 用 `>=` / `<=`（含等号）
- `FixedTakeProfitPolicy` / `SignalExitPolicy` 依赖 `profit_level` 的严格 `>`（不含等号）
- `LayeredReducePolicy` 至少两层浮亏才触发，触发后对全部 `quantity > 0` 且
  `ratio < 0` 的层产出决策，恒为 force
"""

import pytest
from pydantic import ValidationError

from smart_trader.exceptions import SmartTraderError
from smart_trader.model import (
    MarketSignal,
    OrderSide,
    OrderStatus,
    PositionSide,
    SignalDirection,
    SignalKind,
)
from smart_trader.strategy.entry_gate import EntryContext
from smart_trader.strategy.exit_policy import (
    ExitContext,
    ExitDecision,
    ExitPolicyChain,
    FixedTakeProfitPolicy,
    ForceExitPolicy,
    LayeredReducePolicy,
    RestingTakeProfitPolicy,
    SignalExitPolicy,
    StopLossPolicy,
)
from smart_trader.strategy.grid_position import GridPosition
from tests.fakes import make_market_signal, make_order_record, make_position_row

# ---- ExitContext / ExitDecision ----


def test_exit_context_requires_close():
    """close 必填: 无收盘价的上下文是危险数据, 构造时立即报错"""
    with pytest.raises(ValidationError, match="close"):
        ExitContext()  # type: ignore[call-arg]


def test_exit_context_defaults_to_no_signal_and_no_positions():
    """默认值: 无信号, 无仓位（close 为必填字段, 不设默认值）"""
    ctx = ExitContext(close=0.5)

    assert ctx.market_signal is None, "默认无信号"
    assert ctx.positions == [], "默认无仓位"


def test_exit_decision_defaults_to_no_exit_and_no_force():
    """默认决策: 不离场, 不强制"""
    decision = ExitDecision()

    assert decision.should_exit is False, "默认不离场"
    assert decision.force is False, "默认不强制"


def test_exit_decision_rejects_unknown_field():
    """extra=forbid: 字段拼写错误立即报错"""
    with pytest.raises(ValueError):
        ExitDecision.model_validate({"should_exit": True, "forced": True})


# ---- ForceExitPolicy ----


def test_force_exit_policy_triggers_when_trigger_true():
    """外部强制（达到上限止损 / 手动全平）"""
    policy = ForceExitPolicy(lambda: True)

    decision = policy.evaluate(_position(), ExitContext(close=0.0))

    assert decision.should_exit is True, "触发时应离场"
    assert decision.force is True, "强制离场必须能覆盖休息中的挂单"


def test_force_exit_policy_does_not_trigger_when_trigger_false():
    """未触发时不离场"""
    policy = ForceExitPolicy(lambda: False)

    decision = policy.evaluate(_position(), ExitContext(close=0.0))

    assert decision.should_exit is False
    assert decision.force is False


def test_force_exit_policy_evaluates_trigger_on_every_call():
    """trigger 是回调, 每次求值都重新读取（状态变化能立即反映）"""
    state = {"active": False}
    policy = ForceExitPolicy(lambda: state["active"])
    position = _position()

    assert policy.evaluate(position, ExitContext(close=0.0)).should_exit is False

    state["active"] = True
    assert policy.evaluate(position, ExitContext(close=0.0)).should_exit is True, (
        "触发状态变化后应立即生效"
    )


# ---- StopLossPolicy ----


def test_stop_loss_policy_triggers_below_stop_price_for_long():
    """多头: 价格跌破 固定止损价(仓位均价 × (1 − rate))"""
    policy = StopLossPolicy()
    position = _holding(enable_stop_loss=True, stop_loss_rate=0.1)

    decision = policy.evaluate(position, ExitContext(close=0.44))

    assert decision.should_exit is True, "跌破止损价应离场"
    assert decision.force is True, "止损属强制离场"


def test_stop_loss_policy_does_not_trigger_above_stop_price_for_long():
    """多头: 价格未跌破止损价"""
    policy = StopLossPolicy()
    position = _holding(enable_stop_loss=True, stop_loss_rate=0.1)

    assert policy.evaluate(position, ExitContext(close=0.46)).should_exit is False


def test_stop_loss_policy_triggers_at_exactly_stop_price_for_long():
    """多头: 恰好等于止损价即触发（含等号）"""
    policy = StopLossPolicy()
    position = _holding(enable_stop_loss=True, stop_loss_rate=0.1)

    assert policy.evaluate(position, ExitContext(close=0.45)).should_exit is True, (
        "止损判定含等号"
    )


def test_stop_loss_policy_triggers_above_stop_price_for_short():
    """空头: 价格涨破止损价（均价 × (1 + rate)）"""
    policy = StopLossPolicy()
    position = _holding(
        position_side=PositionSide.SHORT,
        enable_stop_loss=True,
        stop_loss_rate=0.1,
    )

    assert policy.evaluate(position, ExitContext(close=0.56)).should_exit is True, (
        "空头涨破止损价应离场"
    )


def test_stop_loss_policy_at_exactly_stop_price_for_short():
    """空头: 恰好等于止损价即触发（含等号）"""
    policy = StopLossPolicy()
    position = _holding(
        position_side=PositionSide.SHORT,
        enable_stop_loss=True,
        stop_loss_rate=0.1,
    )

    assert policy.evaluate(position, ExitContext(close=0.55)).should_exit is True


def test_stop_loss_policy_ignores_position_without_stop_loss():
    """仓位未启用止损时不触发（隔离）"""
    policy = StopLossPolicy()
    position = _holding(enable_stop_loss=False, stop_loss_rate=0.1)

    assert policy.evaluate(position, ExitContext(close=0.1)).should_exit is False, (
        "未启用止损的仓位不应被止损策略平仓"
    )


def test_stop_loss_policy_rate_is_live_parameter():
    """事故回归（Step 22）: stop_loss_rate 是活参数 —— 同一收盘价下,
    改费率（改库或改配置）后触发判定立即随之变化"""
    policy = StopLossPolicy()
    loose = _holding(enable_stop_loss=True, stop_loss_rate=0.005)
    tight = _holding(enable_stop_loss=True, stop_loss_rate=0.05)

    assert policy.evaluate(loose, ExitContext(close=0.48)).should_exit is True, (
        "0.5% 止损触发价 0.4975, 收盘 0.48 应触发"
    )
    assert policy.evaluate(tight, ExitContext(close=0.48)).should_exit is False, (
        "5% 止损触发价 0.475, 收盘 0.48 不应触发"
    )


def test_stop_loss_policy_fixed_stop_matches_incident_expectation():
    """事故回归（Step 22）: 均价 0.08783 × (1 − 0.005) = 0.08739 必须触发,
    而不是被开仓时烘焙的 0.0834 死快照挡住"""
    policy = StopLossPolicy()
    position = _holding(
        entry_price=0.08783, enable_stop_loss=True, stop_loss_rate=0.005
    )

    assert policy.evaluate(position, ExitContext(close=0.08739)).should_exit is True, (
        "恰好等于 0.5% 止损价应触发（含等号）"
    )
    assert policy.evaluate(position, ExitContext(close=0.0875)).should_exit is False, (
        "止损价上方不应触发"
    )


def test_stop_loss_policy_without_cost_basis_does_not_trigger():
    """无成本基准（无成交且无开仓参考价）时无法判定止损（空值）"""
    policy = StopLossPolicy()
    position = _position(enable_stop_loss=True, stop_loss_rate=0.1, opening_price=0.0)

    assert policy.evaluate(position, ExitContext(close=0.44)).should_exit is False, (
        "均价为 0 的仓位没有成本基准, 不应触发止损"
    )


def test_stop_loss_policy_trailing_mode_uses_ratchet_field():
    """跟踪止损: 读 ratchet 字段而非费率实时计算"""
    policy = StopLossPolicy()
    position = _holding(
        enable_stop_loss=True,
        enable_trailing_stop=True,
        stop_loss_rate=0.005,
        trailing_stop_price=0.45,
    )

    assert policy.evaluate(position, ExitContext(close=0.48)).should_exit is False, (
        "费率计算的 0.4975 不参与判定, ratchet 字段 0.45 才是权威"
    )
    assert policy.evaluate(position, ExitContext(close=0.44)).should_exit is True, (
        "跌破 ratchet 字段应触发"
    )


def test_stop_loss_policy_trailing_mode_without_field_does_not_trigger():
    """跟踪止损模式但 ratchet 字段缺失时不触发（空值, 不回退费率计算）"""
    policy = StopLossPolicy()
    position = _holding(
        enable_stop_loss=True,
        enable_trailing_stop=True,
        stop_loss_rate=0.005,
        trailing_stop_price=None,
    )

    assert policy.evaluate(position, ExitContext(close=0.1)).should_exit is False, (
        "字段缺失时无法判定跟踪止损"
    )


# ---- FixedTakeProfitPolicy ----


def test_fixed_take_profit_policy_triggers_above_fixed_rate():
    """盈利超过固定止盈率时离场"""
    policy = FixedTakeProfitPolicy()
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)

    decision = policy.evaluate(position, ExitContext(close=0.51))

    assert decision.should_exit is True, "超过止盈率应离场"
    assert decision.force is False, "止盈不强制, 保留休息中的挂单"


def test_fixed_take_profit_policy_does_not_trigger_exactly_at_fixed_rate():
    """恰好等于固定止盈率时不触发（profit_level 阈值不含等号）"""
    policy = FixedTakeProfitPolicy()
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)

    assert policy.evaluate(position, ExitContext(close=0.505)).should_exit is False, (
        "profit_level 阈值不含等号: 恰好等于止盈价仍是 1 级"
    )


def test_fixed_take_profit_policy_does_not_trigger_when_losing():
    """亏损时不止盈"""
    policy = FixedTakeProfitPolicy()
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)

    assert policy.evaluate(position, ExitContext(close=0.49)).should_exit is False


def test_fixed_take_profit_policy_ignores_position_without_price():
    """均价不可用（<=0）时不止盈"""
    policy = FixedTakeProfitPolicy()
    position = _position(opening_price=0.0, opening_quantity=0.0)

    assert policy.evaluate(position, ExitContext(close=0.5)).should_exit is False


def test_fixed_take_profit_policy_uses_opposite_direction_for_short():
    """空头盈利方向与多头相反: 价格下跌才是盈利

    `profit_level` 对 SHORT 用 `<` 比较 `price × (1 - rate)`, 因此
    close < 入场价达阈值时触发止盈。这一口径由本测试锁定。
    """
    policy = FixedTakeProfitPolicy()
    position = _position(
        position_side=PositionSide.SHORT,
        fixed_take_profit_rate=0.01,
        opening_price=0.5,
    )

    falling = policy.evaluate(position, ExitContext(close=0.49))
    rising = policy.evaluate(position, ExitContext(close=0.51))

    assert falling.should_exit is True, "空头价格下跌 2% 应止盈"
    assert rising.should_exit is False, "空头价格上涨应为亏损, 不止盈"


# ---- SignalExitPolicy ----


def test_signal_exit_policy_triggers_in_trigger_mode():
    """无方向倾向: 任何非中性信号都可离场"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)

    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        ctx = ExitContext(close=0.505, market_signal=_signal(direction))
        assert policy.evaluate(position, ctx).should_exit is True, (
            f"TRIGGER 模式下 {direction.value} 信号均可触发离场"
        )


def test_signal_exit_policy_blocks_neutral_in_trigger_mode():
    """无方向倾向模式下中性信号不触发"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)
    ctx = ExitContext(close=0.505, market_signal=_signal(SignalDirection.NEUTRAL))

    assert policy.evaluate(position, ctx).should_exit is False


def test_signal_exit_policy_blocks_when_no_signal_configured():
    """未配置信号时不触发（空值）"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)

    assert policy.evaluate(position, ExitContext(close=0.505)).should_exit is False, (
        "空信号应拦截离场（与入场侧放行相反, 见 02-domain-model.md 第九节）"
    )


def test_signal_exit_policy_in_directional_mode_requires_exit_direction():
    """有方向倾向: 仅当信号方向与出场方向一致时离场

    多头仓位的出场方向是 SELL, 因此需要看跌信号。
    """
    policy = SignalExitPolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    position = _position(opening_price=0.5)

    matching = ExitContext(close=0.505, market_signal=_signal(SignalDirection.SHORT))
    opposite = ExitContext(close=0.505, market_signal=_signal(SignalDirection.LONG))

    assert policy.evaluate(position, matching).should_exit is True, (
        "看跌信号与出场方向一致, 应离场"
    )
    assert policy.evaluate(position, opposite).should_exit is False, (
        "看涨信号与出场方向相反, 不应离场"
    )


def test_signal_exit_policy_in_directional_mode_uses_exit_side_for_short():
    """空头仓位的出场方向是 BUY, 因此需要看涨信号"""
    policy = SignalExitPolicy(SignalKind.DIRECTIONAL, OrderSide.BUY)
    position = _position(position_side=PositionSide.SHORT, opening_price=0.5)

    matching = ExitContext(close=0.495, market_signal=_signal(SignalDirection.LONG))
    opposite = ExitContext(close=0.495, market_signal=_signal(SignalDirection.SHORT))

    assert policy.evaluate(position, matching).should_exit is True
    assert policy.evaluate(position, opposite).should_exit is False


def test_signal_exit_policy_is_never_force():
    """信号离场恒为非强制（保留休息中的挂单）"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)
    ctx = ExitContext(close=0.505, market_signal=_signal(SignalDirection.LONG))

    assert policy.evaluate(position, ctx).force is False, (
        "信号离场不得覆盖休息中的出场挂单"
    )


def test_signal_exit_policy_triggers_on_any_floating_profit():
    """止盈 = 有浮盈即走, 无盈利门槛（Step 24）"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)

    ctx = ExitContext(close=0.5001, market_signal=_signal(SignalDirection.LONG))
    assert policy.evaluate(position, ctx).should_exit is True, (
        "0.02% 的浮盈也是浮盈, 应止盈"
    )


def test_signal_exit_policy_skips_breakeven_and_loss():
    """持平与浮亏不止盈（止盈的字面语义）"""
    policy = SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)
    position = _position(opening_price=0.5)

    breakeven = ExitContext(close=0.5, market_signal=_signal(SignalDirection.LONG))
    losing = ExitContext(close=0.4999, market_signal=_signal(SignalDirection.LONG))

    assert policy.evaluate(position, breakeven).should_exit is False, (
        "恰好持平没有浮盈, 不止盈"
    )
    assert policy.evaluate(position, losing).should_exit is False, (
        "浮亏不属于止盈分支"
    )


# ---- LayeredReducePolicy ----


def test_layered_reduce_requires_at_least_two_losing_layers():
    """少于两层浮亏不触发: 单层小亏在震荡中是噪声"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    lost = _holding("LOST", entry_price=0.6)
    profitable = _holding("PROFIT", entry_price=0.5)
    ctx = _exit_ctx([profitable, lost], close=0.58, direction=SignalDirection.SHORT)

    assert policy.evaluate(lost, ctx).should_exit is False, (
        "只有一层浮亏时不应触发信号止损"
    )
    assert policy.evaluate(profitable, ctx).should_exit is False


def test_layered_reduce_stops_all_losing_layers():
    """两层及以上浮亏: 全部浮亏层一次性离场, 浮盈层不动"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.58)
    profitable = _holding("PROFIT", entry_price=0.5)
    ctx = _exit_ctx(
        [profitable, shallow, deep], close=0.55, direction=SignalDirection.SHORT
    )

    for position in (deep, shallow):
        decision = policy.evaluate(position, ctx)
        assert decision.should_exit is True, f"{position.id} 浮亏, 应一次性止损"
        assert decision.force is True, "逆势减仓必须能覆盖休息中的挂单"
    assert policy.evaluate(profitable, ctx).should_exit is False, (
        "浮盈层不归止损分支"
    )


def test_layered_reduce_ignores_breakeven_layers():
    """持平层不计入浮亏层数, 也不被止损"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.57)
    flat = _holding("FLAT", entry_price=0.55)
    ctx = _exit_ctx(
        [deep, shallow, flat], close=0.55, direction=SignalDirection.SHORT
    )

    assert policy.evaluate(deep, ctx).should_exit is True
    assert policy.evaluate(shallow, ctx).should_exit is True
    assert policy.evaluate(flat, ctx).should_exit is False, "持平不属于浮亏"


def test_layered_reduce_is_force():
    """批量止损恒为强制离场（撤单重挂到最新价, 保证实际减仓）"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.58)
    ctx = _exit_ctx([shallow, deep], close=0.55, direction=SignalDirection.SHORT)

    for position in (deep, shallow):
        decision = policy.evaluate(position, ctx)
        assert decision.should_exit is True
        assert decision.force is True, "逆势减仓必须能覆盖休息中的挂单"


def test_layered_reduce_requires_opposite_direction():
    """DIRECTIONAL: 未命中出场方向（同向/中性）不减仓"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.58)

    same_direction = _exit_ctx(
        [shallow, deep], close=0.55, direction=SignalDirection.LONG
    )
    neutral = _exit_ctx(
        [shallow, deep], close=0.55, direction=SignalDirection.NEUTRAL
    )

    for ctx in (same_direction, neutral):
        for position in (deep, shallow):
            assert policy.evaluate(position, ctx).should_exit is False, (
                "看涨/中性信号与出场方向不符, 不减仓"
            )


def test_layered_reduce_trigger_kind_accepts_any_non_neutral():
    """TRIGGER: 任何非中性信号都可触发批量减仓"""
    policy = LayeredReducePolicy(SignalKind.TRIGGER, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.58)

    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        ctx = _exit_ctx([shallow, deep], close=0.55, direction=direction)
        for position in (deep, shallow):
            assert policy.evaluate(position, ctx).should_exit is True, (
                f"TRIGGER 模式下 {direction.value} 信号均可触发减仓"
            )


def test_layered_reduce_does_not_exit_without_signal():
    """未配置信号时不减仓（空值）"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.58)

    ctx = ExitContext(positions=[shallow, deep], close=0.55)
    for position in (deep, shallow):
        assert policy.evaluate(position, ctx).should_exit is False, (
            "空信号不得触发减仓"
        )


def test_layered_reduce_requires_floating_loss():
    """止损 = 浮亏才走: 全部浮盈时即使有两层也不触发"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    first = _holding("FIRST", entry_price=0.4)
    second = _holding("SECOND", entry_price=0.5)
    ctx = _exit_ctx([first, second], close=0.55, direction=SignalDirection.SHORT)

    for position in (first, second):
        assert policy.evaluate(position, ctx).should_exit is False, (
            "浮盈层不应被信号止损剥掉（止盈/固定止盈的职责）"
        )


def test_layered_reduce_short_stops_all_losing_layers():
    """做空对称: 全部浮亏层（入场价低于现价）一次性离场"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.BUY)
    low = _holding("LOW", position_side=PositionSide.SHORT, entry_price=0.5)
    middle = _holding("MIDDLE", position_side=PositionSide.SHORT, entry_price=0.52)
    profitable = _holding(
        "PROFIT", position_side=PositionSide.SHORT, entry_price=0.6
    )
    ctx = _exit_ctx(
        [profitable, low, middle], close=0.55, direction=SignalDirection.LONG
    )

    assert policy.evaluate(low, ctx).should_exit is True, "做空浮亏层应止损"
    assert policy.evaluate(middle, ctx).should_exit is True, "做空浮亏层应止损"
    assert policy.evaluate(profitable, ctx).should_exit is False, (
        "做空浮盈层不归止损分支"
    )


def test_layered_reduce_ignores_empty_layers_in_selection():
    """零数量层不参与浮亏统计, 也不被止损"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    empty = _holding("EMPTY", entry_price=0.7, quantity=0.0)
    deep = _holding("DEEP", entry_price=0.6)
    shallow = _holding("SHALLOW", entry_price=0.55)
    ctx = _exit_ctx([deep, shallow, empty], close=0.5, direction=SignalDirection.SHORT)

    assert policy.evaluate(deep, ctx).should_exit is True
    assert policy.evaluate(shallow, ctx).should_exit is True
    assert policy.evaluate(empty, ctx).should_exit is False, "零数量层不得离场"


def test_layered_reduce_keeps_selecting_layer_with_pending_exit():
    """浮亏层已有未成交出场单时仍被选中（force 撤单重挂）"""
    policy = LayeredReducePolicy(SignalKind.DIRECTIONAL, OrderSide.SELL)
    deep = _holding("DEEP", entry_price=0.6)
    deep.orders.append(
        make_order_record(
            "o-DEEP-exit",
            "DEEP",
            side=OrderSide.SELL,
            price=0.6,
            quantity=100.0,
            status=OrderStatus.OPEN,
        )
    )
    shallow = _holding("SHALLOW", entry_price=0.58)
    ctx = _exit_ctx([shallow, deep], close=0.55, direction=SignalDirection.SHORT)

    assert deep.has_pending_exit is True, "前置条件: DEEP 应有未成交出场单"
    decision = policy.evaluate(deep, ctx)
    assert decision.should_exit is True, "仍选中在减仓中的浮亏层"
    assert decision.force is True, "force 才能撤单重挂"
    assert policy.evaluate(shallow, ctx).should_exit is True, (
        "其余浮亏层同样一次性止损"
    )


# ---- ExitPolicyChain ----


def test_chain_exits_when_any_policy_wants_exit():
    """任一策略要求离场则离场"""
    position = _holding(enable_stop_loss=True, stop_loss_rate=0.1)
    chain = ExitPolicyChain([FixedTakeProfitPolicy(), StopLossPolicy()])

    decision = chain.decide(position, ExitContext(close=0.44))

    assert decision.should_exit is True, "止损策略应触发离场"


def test_chain_does_not_exit_when_no_policy_wants_exit():
    """全部策略都不要求离场则不离场"""
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)
    chain = ExitPolicyChain([FixedTakeProfitPolicy(), StopLossPolicy()])

    decision = chain.decide(position, ExitContext(close=0.505))

    assert decision.should_exit is False


def test_chain_merges_force_from_any_policy():
    """force 由任一策略贡献"""
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)
    chain = ExitPolicyChain([FixedTakeProfitPolicy(), ForceExitPolicy(lambda: True)])

    decision = chain.decide(position, ExitContext(close=0.51))

    assert decision.should_exit is True
    assert decision.force is True, "强制标记应被合并进来"


def test_chain_merge_is_order_independent():
    """合并顺序无关: 交换策略顺序结果相同（不变量 9 的基础）"""
    position = _holding(enable_stop_loss=True, stop_loss_rate=0.1)
    stop = StopLossPolicy()
    force = ForceExitPolicy(lambda: True)
    ctx = ExitContext(close=0.44)

    first = ExitPolicyChain([stop, force]).decide(position, ctx)
    second = ExitPolicyChain([force, stop]).decide(position, ctx)

    assert first == second, "策略顺序不应影响合并结果"


def test_empty_chain_never_exits():
    """空链恒不离场（无策略即无离场条件）"""
    position = _position(enable_stop_loss=True, trailing_stop_price=0.45)

    decision = ExitPolicyChain([]).decide(position, ExitContext(close=0.1))

    assert decision.should_exit is False, "空策略链不应平仓"


def test_chain_does_not_leak_state_between_positions():
    """隔离: 同一策略链对不同仓位的决策互不影响"""
    stop = StopLossPolicy()
    chain = ExitPolicyChain([stop])
    triggered = _holding("TRIGGERED", enable_stop_loss=True, stop_loss_rate=0.1)
    safe = _holding("SAFE", enable_stop_loss=True, stop_loss_rate=0.1)

    assert chain.decide(triggered, ExitContext(close=0.44)).should_exit is True
    assert chain.decide(safe, ExitContext(close=0.46)).should_exit is False, (
        "前一仓位的决策不应影响后一仓位"
    )


# ---- RestingTakeProfitPolicy ----


def test_resting_take_profit_price_uses_position_rate():
    """挂单价取自仓位的固定止盈率（与 profit_level 同源, 避免第二真相）"""
    policy = RestingTakeProfitPolicy(OrderSide.BUY)
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)

    assert policy.limit_price(position) == 0.505, (
        "买入方向止盈价 = 均价 × (1 + 止盈率)"
    )


def test_resting_take_profit_price_for_sell():
    """卖出方向止盈价向下"""
    policy = RestingTakeProfitPolicy(OrderSide.SELL)
    position = _position(fixed_take_profit_rate=0.01, opening_price=0.5)

    assert policy.limit_price(position) == 0.495, (
        "卖出方向止盈价 = 均价 × (1 − 止盈率)"
    )


def test_resting_take_profit_price_for_short_position():
    """空头仓位的止盈方向由入场方向决定, 与仓位实际方向一致"""
    policy = RestingTakeProfitPolicy(OrderSide.SELL)
    position = _position(
        position_side=PositionSide.SHORT,
        fixed_take_profit_rate=0.01,
        opening_price=0.5,
    )

    assert policy.limit_price(position) == 0.495


# ---- helpers ----


def _position(position_id: str = "POS1", **overrides: object) -> GridPosition:
    row = make_position_row(position_id, **overrides)
    return GridPosition.from_row(row, [])


def _holding(
    position_id: str = "POS1",
    *,
    entry_price: float = 0.5,
    quantity: float = 100.0,
    **row_overrides: object,
) -> GridPosition:
    """构造持仓中的逻辑仓位（附一条按 `quantity` 成交的入场单）

    批量减仓的选中口径要求 `quantity > 0`, 因此不能用 `_position`（无成交）。
    """
    row = make_position_row(position_id, **row_overrides)
    entry_side = (
        OrderSide.BUY if row.position_side == PositionSide.LONG else OrderSide.SELL
    )
    order = make_order_record(
        f"o-{position_id}",
        position_id,
        side=entry_side,
        price=entry_price,
        quantity=quantity,
        filled_qty=quantity,
        avg_price=entry_price,
        status=OrderStatus.CLOSED,
    )
    return GridPosition.from_row(row, [order])


def _exit_ctx(
    positions: list[GridPosition],
    close: float,
    direction: SignalDirection,
) -> ExitContext:
    return ExitContext(
        positions=positions, close=close, market_signal=make_market_signal(direction)
    )


def _signal(direction: SignalDirection) -> MarketSignal:
    return make_market_signal(direction)


def test_entry_gate_context_and_exit_context_are_distinct_types():
    """两个上下文互不通用, 防止字段被误用"""
    assert EntryContext is not ExitContext, "入场与离场上下文必须是不同类型"


def test_require_close_error_is_a_smart_trader_error():
    """EntryContext.require_close 抛业务异常而非 ValueError"""
    with pytest.raises(SmartTraderError) as caught:
        EntryContext().require_close()

    assert caught.value.error_code.value == "market_data_unavailable", (
        "应使用 market_data_unavailable 异常码"
    )