"""SignalGridStrategy 的信号装配路径测试

覆盖本次重构新增的编排逻辑：闸门链/策略链装配、每根 K 线只求值一次信号、
信号离场与休息挂单的优先级、手动全平不再触发止损回调。

`tests/test_signal_grid_strategy.py` 已覆盖开仓条件、孤儿回收等既有分支；
本文件只补信号相关路径（重构前这两处逻辑零覆盖）。
"""

from smart_trader.model import (
    OrderResult,
    OrderSide,
    OrderStatus,
    PositionSide,
    SignalDirection,
    SignalKind,
)
from smart_trader.strategy.signal_grid_strategy import GridPosition, SignalGridStrategy
from tests.fakes import DEFAULT_SYMBOL, FakeSignal
from tests.test_signal_grid_strategy import (
    TIMESTAMP,
    build_strategy,
    push_kline,
    seed_position,
)


def _long(**overrides: object):
    """多头 + TRIGGER 信号; 可用 signal= 覆盖信号实例

    @note 离场阈值随仓位落库, 涉及阈值的用例在 seed_position 时指定
    """
    fields: dict[str, object] = {"signal_kind": SignalKind.TRIGGER}
    fields.setdefault("signal", FakeSignal())
    fields.update(overrides)
    return build_strategy(**fields)  # pyright: ignore[reportArgumentType]


def _layered_reduce(**overrides: object):
    """多头 + 信号止损（逐层减仓）; 默认反向信号（看跌）

    @note 信号止盈显式关闭, 保持"只测信号止损"的语义
    """
    fields: dict[str, object] = {
        "signal_kind": SignalKind.DIRECTIONAL,
        "enable_signal_take_profit": False,
        "enable_signal_stop_loss": True,
        "signal": FakeSignal([SignalDirection.SHORT]),
    }
    fields.update(overrides)
    return build_strategy(**fields)  # pyright: ignore[reportArgumentType]


def _position_by_id(strategy: SignalGridStrategy, position_id: str) -> GridPosition:
    return next(
        position for position in strategy.positions if position.id == position_id
    )


# ---- 入场: 信号闸门 ----


def test_opens_position_when_trigger_signal_present():
    """TRIGGER 模式下非中性信号许可入场"""
    strategy, trading, _, _ = _long()
    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1, "有非中性信号时应开仓"
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_skips_open_when_signal_is_neutral():
    """TRIGGER 模式下中性信号拦截入场"""
    strategy, trading, _, _ = _long(signal=FakeSignal([SignalDirection.NEUTRAL]))
    push_kline(strategy, 0.5)

    assert strategy.positions == [], "中性信号不应开仓"
    assert trading.placed == [], "不应产生任何委托"


def test_skips_open_when_no_signal_configured():
    """未配置信号时入场判定放行（既有语义: 无信号 = 不限制）"""
    strategy, trading, _, _ = build_strategy()

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1, "未配置信号不应成为入场限制"
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_directional_kind_blocks_opposite_direction_signal():
    """DIRECTIONAL 模式下反向信号拦截入场"""
    strategy, trading, _, _ = build_strategy(
        signal=FakeSignal([SignalDirection.SHORT]),
        signal_kind=SignalKind.DIRECTIONAL,
    )

    push_kline(strategy, 0.5)

    assert strategy.positions == [], "看跌信号不应许可买入入场"
    assert trading.placed == []


def test_directional_kind_allows_matching_direction_signal():
    """DIRECTIONAL 模式下同向信号许可入场"""
    strategy, _, _, _ = build_strategy(
        signal=FakeSignal([SignalDirection.LONG]),
        signal_kind=SignalKind.DIRECTIONAL,
    )

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1, "看涨信号应许可买入入场"


def test_directional_kind_uses_entry_side_for_short_position():
    """DIRECTIONAL 模式下空头仓位以 SELL 入场, 需看跌信号"""
    strategy, _, _, _ = build_strategy(
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        signal=FakeSignal([SignalDirection.SHORT]),
        signal_kind=SignalKind.DIRECTIONAL,
    )

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1, "空头仓位看跌信号应许可卖出入场"
    assert strategy.positions[0].position_side is PositionSide.SHORT


# ---- 离场: 信号策略 ----


def test_signal_exit_closes_profitable_position_on_reverse_signal():
    """信号止盈: 浮盈层遇反向信号即平仓（无盈利门槛, Step 24）"""
    strategy, trading, _, _ = _long()
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert [request.side for request in trading.placed] == [OrderSide.SELL], (
        "浮盈层遇反向信号应挂出出场单"
    )


def test_no_signal_exit_when_position_at_loss():
    """浮亏层不走信号止盈分支（止盈的字面语义, Step 24）

    注意: 止损分支由 enable_signal_stop_loss 单独控制, 此处未开启。
    跌幅收窄在间距(1%)之内, 避免间距闸门放行反向加仓干扰断言。
    """
    strategy, trading, _, _ = _long()
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.4999)

    assert trading.placed == [], "浮亏层不应触发信号止盈"


def test_no_signal_exit_when_signal_is_neutral():
    """中性信号不触发离场"""
    strategy, trading, _, _ = _long(
        signal=FakeSignal([SignalDirection.NEUTRAL]),
        fixed_rate_take_profit=False,
    )
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert trading.placed == [], "中性信号不应平仓"


def test_no_signal_exit_when_signal_policies_disabled():
    """信号止盈与信号止损都关闭时不因信号平仓（隔离）"""
    strategy, trading, _, _ = _long(
        enable_signal_take_profit=False, enable_signal_stop_loss=False
    )
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert trading.placed == [], "两个信号离场开关全关时信号不得平仓"


def test_no_signal_exit_when_signal_is_none():
    """未配置信号时不因信号平仓（空值）"""
    strategy, trading, _, _ = build_strategy()
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert trading.placed == [], "空信号不得触发离场"


def test_directional_signal_exit_requires_opposite_direction():
    """DIRECTIONAL 模式下需反向信号才离场"""
    strategy, trading, _, _ = build_strategy(
        signal=FakeSignal([SignalDirection.LONG]),
        signal_kind=SignalKind.DIRECTIONAL,
    )
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert trading.placed == [], "看涨信号与出场方向(卖出)相反, 不应平仓"


# ---- 离场: 逆势逐层减仓 ----

def test_layered_reduce_peels_only_outermost_layer():
    """逆势减仓: 反向信号命中时只挂最外层一层的出场单, 其余层不动"""
    strategy, trading, _, _ = _layered_reduce()
    seed_position(strategy, "INNER", entry_price=0.58, quantity=100.0)
    outer = seed_position(strategy, "OUTER", entry_price=0.6, quantity=100.0)

    push_kline(strategy, 0.55)

    assert [request.side for request in trading.placed] == [OrderSide.SELL], (
        "只应挂出一张出场单"
    )
    assert outer.has_pending_exit is True, "最外层应被减仓"
    assert _position_by_id(strategy, "INNER").has_pending_exit is False, (
        "内层不得被同时平仓"
    )


def test_layered_reduce_waits_before_peeling_next_layer():
    """最外层仍在减仓中（出场单未成交）时, 下一层等待, 不齐平"""
    strategy, trading, _, _ = _layered_reduce()
    seed_position(strategy, "INNER", entry_price=0.58, quantity=100.0)
    seed_position(strategy, "OUTER", entry_price=0.6, quantity=100.0)

    push_kline(strategy, 0.55, timestamp=TIMESTAMP)
    placed_before = len(trading.placed)

    push_kline(strategy, 0.54, timestamp=TIMESTAMP + 60_000)

    assert len(trading.placed) > placed_before, (
        "最外层出场单未成交时 force 应撤单重挂（继续尝试减仓）"
    )
    assert all(request.side is OrderSide.SELL for request in trading.placed), (
        "减仓路径不得产生入场单"
    )
    assert _position_by_id(strategy, "INNER").has_pending_exit is False, (
        "最外层未走完之前不得剥下一层"
    )
    assert _position_by_id(strategy, "OUTER").has_pending_exit is True


def test_layered_reduce_peels_next_layer_after_outermost_closed():
    """最外层出场单成交归档后, 同一根 K 线轮到次外层（仍浮亏, 接力减仓）"""
    strategy, trading, _, _ = _layered_reduce()
    seed_position(strategy, "INNER", entry_price=0.58, quantity=100.0)
    outer = seed_position(strategy, "OUTER", entry_price=0.6, quantity=100.0)

    push_kline(strategy, 0.55, timestamp=TIMESTAMP)
    exit_order = outer.pending_exit_order
    assert exit_order is not None, "前置条件: 最外层应已挂出出场单"

    # 让最外层的出场单在下一根 K 线被观察为完全成交 -> 结算归档
    trading.queue_query(
        OrderResult(
            custom_id=exit_order.order_id,
            symbol=DEFAULT_SYMBOL,
            side=OrderSide.SELL,
            status=OrderStatus.CLOSED,
            quantity=exit_order.quantity,
            filled_qty=exit_order.quantity,
            price=exit_order.price,
        )
    )
    push_kline(strategy, 0.54, timestamp=TIMESTAMP + 60_000)

    assert [position.id for position in strategy.positions] == ["INNER"], (
        "最外层成交后应归档, 只剩内层"
    )
    assert _position_by_id(strategy, "INNER").has_pending_exit is True, (
        "归档后次外层应接力减仓"
    )
    assert [request.side for request in trading.placed] == [
        OrderSide.SELL,
        OrderSide.SELL,
    ], "两根 K 线各挂出一张出场单, 均为减仓方向"


# ---- 优先级: 信号离场 vs 休息挂单 ----


def test_signal_exit_keeps_resting_exit_order_instead_of_replacing():
    """已有休息中的出场单时, 信号离场保留原挂单（force=False）"""
    strategy, trading, _, _ = _long()
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    # 第一根: 固定止盈先挂出出场单（需开启固定止盈）
    strategy2, trading2, _, _ = _long(fixed_rate_take_profit=True)
    seed_position(strategy2, entry_price=0.5, quantity=100.0)
    push_kline(strategy2, 0.505)
    placed_before = len(trading2.placed)
    assert strategy2.positions[0].has_pending_exit, "应已挂出出场单"

    # 后续 K 线: 信号仍然命中, 但不重复下单
    push_kline(strategy2, 0.505)
    push_kline(strategy2, 0.505)

    assert len(trading2.placed) == placed_before, (
        "已有未成交出场单时, 普通止盈不得重复下单"
    )
    assert trading.placed == []


def test_stop_loss_replaces_resting_exit_order():
    """止损属强制离场: 覆盖休息中的出场单（force=True）"""
    strategy, trading, _, _ = _long(
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)
    assert strategy.positions[0].has_pending_exit, "应已挂出休息中的出场单"
    placed_before = len(trading.placed)

    # 价格跌破止损价 -> 强制离场
    push_kline(strategy, 0.47)

    assert len(trading.placed) > placed_before, "强制离场应产生新的委托动作"


# ---- 幂等: 每根 K 线只求值一次 ----


def test_signal_is_evaluated_at_most_once_per_kline():
    """同一根 K 线内信号最多求值一次（策略级 memo）"""
    signal = FakeSignal([SignalDirection.LONG])
    strategy, _, _, _ = build_strategy(signal=signal, signal_kind=SignalKind.TRIGGER)
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)

    assert signal.evaluate_calls <= 1, (
        f"同一根 K 线不应重复求值, 实际调用 {signal.evaluate_calls} 次"
    )


def test_signal_is_evaluated_again_on_next_kline():
    """K 线推进时重新求值信号"""
    signal = FakeSignal([SignalDirection.LONG])
    strategy, _, _, _ = build_strategy(signal=signal, signal_kind=SignalKind.TRIGGER)
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.5, timestamp=1_700_000_000_000)
    calls_after_first = signal.evaluate_calls
    push_kline(strategy, 0.501, timestamp=1_700_000_060_000)

    assert signal.evaluate_calls > calls_after_first, (
        "K 线时间推进后应重新求值信号"
    )


def test_repeated_klines_do_not_duplicate_exit_orders():
    """连续多根 K 线命中信号时不重复挂出场单"""
    strategy, trading, _, _ = _long()
    seed_position(strategy, entry_price=0.5, quantity=100.0)

    push_kline(strategy, 0.505)
    placed_once = len(trading.placed)

    for close in (0.506, 0.507, 0.508):
        push_kline(strategy, close)

    assert len(trading.placed) == placed_once, (
        "出场单已挂出后, 后续信号命中不得重复下单"
    )