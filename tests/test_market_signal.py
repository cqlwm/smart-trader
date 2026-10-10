"""行情信号值对象与仓位上限测试

覆盖: `SignalDirection` 的方向运算 / `MarketSignal` 默认值 / `PositionLimit` 的
上限判定与"达到上限止损"边界。
"""

import pytest

from smart_trader.model import (
    MarketSignal,
    OrderSide,
    PositionLimit,
    SignalDirection,
    SignalKind,
)

# ---- SignalDirection ----


def test_signal_direction_opposite_flips_long_and_short():
    """LONG / SHORT 互为反向"""
    assert SignalDirection.LONG.opposite() is SignalDirection.SHORT
    assert SignalDirection.SHORT.opposite() is SignalDirection.LONG


def test_signal_direction_opposite_of_neutral_is_neutral():
    """NEUTRAL 的反向仍是 NEUTRAL —— 中性方向没有反向可言"""
    assert SignalDirection.NEUTRAL.opposite() is SignalDirection.NEUTRAL


def test_signal_direction_of_order_side_maps_buy_to_long():
    """买入对应看涨"""
    assert SignalDirection.of_order_side(OrderSide.BUY) is SignalDirection.LONG


def test_signal_direction_of_order_side_maps_sell_to_short():
    """卖出对应看跌"""
    assert SignalDirection.of_order_side(OrderSide.SELL) is SignalDirection.SHORT


def test_signal_direction_to_order_side_round_trips():
    """下单方向 → 行情方向 → 下单方向 往返一致"""
    for side in OrderSide:
        assert SignalDirection.of_order_side(side).to_order_side() is side


def test_signal_direction_values_are_lowercase():
    """枚举取值统一小写，与项目内其它枚举一致"""
    assert [d.value for d in SignalDirection] == ["long", "short", "neutral"]


# ---- MarketSignal ----


def test_market_signal_defaults_to_neutral():
    """未指定方向时为中性, 触发时间为空串"""
    signal = MarketSignal()

    assert signal.direction is SignalDirection.NEUTRAL, "缺省方向必须是中性"
    assert signal.datetime == "", "未触发时时间应为空串"


def test_market_signal_rejects_unknown_field():
    """extra=forbid: 字段拼写错误立即报错, 不被静默忽略"""
    with pytest.raises(ValueError):
        MarketSignal.model_validate({"direction_": "long"})


def test_market_signal_dumps_enum_as_lowercase_string():
    """按 json 模式导出时枚举打成小写 value(便于日志与将来落库)"""
    dumped = MarketSignal(direction=SignalDirection.LONG, datetime="t").model_dump(
        mode="json"
    )

    assert dumped == {"direction": "long", "datetime": "t"}


# ---- SignalKind ----


def test_signal_kind_values_are_lowercase():
    """TRIGGER 无方向倾向; DIRECTIONAL 有方向倾向"""
    assert [k.value for k in SignalKind] == ["trigger", "directional"]


# ---- PositionLimit ----


def test_position_limit_reached_only_at_max_order():
    """仓位数小于上限时未达上限, 恰好等于时才算达到"""
    limit = PositionLimit(max_order=3)

    assert limit.reached(2) is False, "2 个仓位时还有空位"
    assert limit.reached(3) is True, "恰好 3 个仓位即达到上限"
    assert limit.reached(4) is True, "超过上限同样算达到"


def test_position_limit_stop_loss_triggers_on_last_slot():
    """只剩 1 个空位时触发全部止损"""
    limit = PositionLimit(max_order=10, enable_stop_loss=True)

    assert limit.stop_loss_triggered(8) is False, "还有 2 个空位"
    assert limit.stop_loss_triggered(9) is True, "只剩 1 个空位即触发"
    assert limit.stop_loss_triggered(10) is True, "已满仓必然触发"


def test_position_limit_stop_loss_disabled_never_triggers():
    """未启用上限止损时永不触发, 即使已满仓"""
    limit = PositionLimit(max_order=2, enable_stop_loss=False)

    assert limit.stop_loss_triggered(0) is False
    assert limit.stop_loss_triggered(2) is False, "已满仓也不触发(功能未开启)"


def test_position_limit_blocks_first_position_when_max_order_is_one():
    """反直觉的现状行为: max_order=1 且空仓时 stop_loss_triggered 仍为真

    即 1 - 0 <= 1 成立, 策略永不下第一单。该行为由来自既有实现, 本次重构保留。
    """
    limit = PositionLimit(max_order=1, enable_stop_loss=True)

    assert limit.stop_loss_triggered(0) is True, (
        "max_order=1 且空仓时上限止损条件成立, 现状下永不开第一单"
    )


def test_position_limit_isolates_disabled_stop_loss_from_reached():
    """两个判定互不干扰: 关闭止损后 reached 仍正常工作"""
    limit = PositionLimit(max_order=2, enable_stop_loss=False)

    assert limit.reached(2) is True, "止损关闭不影响上限判定"
    assert limit.stop_loss_triggered(1) is False, "止损关闭不触发强制离场"


def test_position_limit_rejects_unknown_field():
    """extra=forbid: 字段拼写错误立即报错"""
    with pytest.raises(ValueError):
        PositionLimit.model_validate({"max_order": 3, "enable_stop_losses": True})
