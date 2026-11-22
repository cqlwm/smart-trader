"""SignalGridStrategy 止损相关测试

覆盖: 单笔止损触发平仓、未触发/未激活时不动作、跟踪止损上移、
空头方向的止损判定、已有出场挂单时不重复下单、暂停后仍跟踪出场单。
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
    TimeInForce,
)
from tests.fakes import (
    DEFAULT_SYMBOL,
    FakeAccountPort,
    make_exchange_position,
    make_order_record,
)
from tests.test_signal_grid_strategy import (
    TIMESTAMP,
    build_strategy,
    push_kline,
    seed_position,
)


def test_stop_loss_triggers_exit_order():
    """跌破止损价 -> 挂出反向平仓单"""
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True, order_stop_loss_rate=0.05
    )
    seed_position(
        strategy,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        trailing_stop_price=0.475,
    )

    push_kline(strategy, 0.47)

    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert strategy.positions[0].pending_exit_order is not None


def test_force_exit_uses_immediately_fillable_behavior():
    """止损属于风控动作: 必须走常规限价, 不能沿用 post-only 追单

    CHASER_OPEN 会先挂 GTX 只做 maker 单, 止损在快速行情里可能挂不上或
    挂在不利方向等待成交。
    """
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
    )
    seed_position(
        strategy,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )

    push_kline(strategy, 0.47)

    assert len(trading.placed) == 1, "止损应只产生一笔平仓委托"
    request = trading.placed[0]
    assert request.time_in_force is not TimeInForce.GTX, (
        f"止损不得使用 post-only GTX, 实际 {request.time_in_force}"
    )
    assert request.time_in_force is None, (
        f"止损应走常规限价单, 实际 time_in_force={request.time_in_force}"
    )
    assert request.quantity == pytest.approx(100.0), "止损应平掉整层可平量"


def test_stop_failure_isolated_per_position():
    """单个仓位止损失败不得中止整根 K 线, 其他仓位的止损必须继续执行"""
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True, order_stop_loss_rate=0.05
    )
    first = seed_position(
        strategy,
        "POS-FAIL",
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )
    second = seed_position(
        strategy,
        "POS-OK",
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )
    trading.queue_place(
        SmartTraderError(
            ErrorCode.ORDER_NOTIONAL_TOO_SMALL,
            "订单名义价值低于交易所最小值",
        )
    )

    push_kline(strategy, 0.47)

    assert len(trading.placed) == 2, (
        f"失败仓位与后续仓位都应尝试下单, 实际 {len(trading.placed)} 笔"
    )
    assert first.pending_exit_order is None, "失败仓位的止损单不应被记录为挂出"
    assert second.pending_exit_order is not None, (
        "前一个仓位止损失败后, 后一个仓位必须继续执行止损"
    )
    assert len(strategy.positions) == 2, (
        "止损执行失败的当根 K 线不得再开新仓（outcome.acted 必须置位）"
    )


def test_stop_loss_not_triggered_keeps_position():
    """未跌破止损价 -> 不挂平仓单"""
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        grid_spacing_rate=0.05,
    )
    seed_position(
        strategy,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        trailing_stop_price=0.475,
    )

    push_kline(strategy, 0.476)

    assert trading.placed == [], "止损未触发不应产生任何委托"
    assert strategy.positions[0].pending_exit_order is None


def test_stop_loss_triggers_for_short_position():
    """空头: 价格涨破止损价 -> 买入平仓"""
    strategy, trading, _, _ = build_strategy(
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    seed_position(
        strategy,
        position_side=PositionSide.SHORT,
        main_order_side=OrderSide.SELL,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        trailing_stop_price=0.525,
    )

    push_kline(strategy, 0.53)

    assert [request.side for request in trading.placed] == [OrderSide.BUY], (
        "空头止损应买入平仓"
    )
    assert strategy.positions[0].pending_exit_order is not None


def test_trailing_stop_moves_up_on_new_high():
    """跟踪止损: 创新高（达到激活盈利）后止损价上移"""
    strategy, trading, _, _ = build_strategy(
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        grid_spacing_rate=0.05,
    )
    position = seed_position(
        strategy,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_rate=0.01,
        trailing_stop_price=0.49,
    )

    push_kline(strategy, 0.52, high=0.52)

    assert position.trailing_stop_price == pytest.approx(0.5096, abs=1e-6), (
        "新止损价 = 最高价 * (1 - 跟踪止损率)"
    )
    assert trading.placed == []


def test_trailing_stop_not_activated_keeps_price():
    """未达到激活盈利 -> 止损价保持不动"""
    strategy, trading, _, _ = build_strategy(
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        grid_spacing_rate=0.05,
    )
    position = seed_position(
        strategy,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_rate=0.01,
        trailing_stop_price=0.49,
    )

    push_kline(strategy, 0.503, high=0.503)

    assert position.trailing_stop_price == 0.49
    assert trading.placed == []


def test_normal_take_profit_keeps_resting_exit_order():
    """已有未成交出场单时, 普通止盈保留原挂单, 不重复下单"""
    strategy, trading, _, _ = build_strategy(
        fixed_rate_take_profit=True, fixed_take_profit_rate=0.01
    )
    position = seed_position(strategy)
    position.orders.append(
        make_order_record(
            "o-EXIT",
            position.id,
            side=OrderSide.SELL,
            price=0.51,
            quantity=100.0,
            filled_qty=0.0,
            status=OrderStatus.OPEN,
        )
    )

    push_kline(strategy, 0.51)

    assert trading.placed == [], "已有休息中的出场挂单, 不得重复下单"
    assert position.quantity == pytest.approx(100.0), "出场未成交, 持仓不变"


def test_pending_exit_is_tracked_even_when_paused():
    """暂停（is_running=False）后仍必须跟踪已挂出的出场单"""
    strategy, trading, _, _ = build_strategy()
    position = seed_position(strategy)
    position.orders.append(
        make_order_record(
            "o-EXIT",
            position.id,
            side=OrderSide.SELL,
            price=0.51,
            quantity=100.0,
            filled_qty=0.0,
            status=OrderStatus.OPEN,
        )
    )
    strategy.is_running = False
    trading.queue_query(
        OrderResult(
            custom_id="o-EXIT",
            symbol=DEFAULT_SYMBOL,
            side=OrderSide.SELL,
            status=OrderStatus.CLOSED,
            quantity=100.0,
            filled_qty=100.0,
            price=0.51,
        )
    )

    push_kline(strategy, 0.51)

    assert strategy.positions == [], (
        "出场成交且可平量归零 -> 归档, 与 is_running 无关"
    )
    assert trading.placed == [], "跟踪出场单不应产生新委托"


# ------------------------------------------------- 多层隔离 / 外部扣减 / 快照


def test_stop_loss_only_closes_triggering_layer():
    """多层仓位: 只有跌破自身止损价的层离场, 其他层不动"""
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True, order_stop_loss_rate=0.05
    )
    triggered = seed_position(
        strategy,
        "POS-TRIGGERED",
        entry_price=0.5,
        quantity=100.0,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )
    safe = seed_position(
        strategy,
        "POS-SAFE",
        entry_price=0.4,
        quantity=100.0,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )

    push_kline(strategy, 0.47)

    assert triggered.pending_exit_order is not None, "跌破 0.475 的层应止损"
    assert safe.pending_exit_order is None, "止损价 0.38 的安全层不得被平"
    assert [request.quantity for request in trading.placed] == [100.0]


def test_stop_loss_quantity_clamped_by_external_reduction():
    """外部平掉部分仓位: 止损出场按剩余可平量下单"""
    account = FakeAccountPort()
    strategy, trading, _, _ = build_strategy(
        account=account,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    seed_position(
        strategy,
        "POS-CLAMP",
        entry_price=0.5,
        quantity=100.0,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )
    account.set_positions(make_exchange_position(60.0))  # 外部已平 40

    push_kline(strategy, 0.47)

    assert [request.quantity for request in trading.placed] == [60.0], (
        f"止损单应按剩余可平量 60 下单, 实际 {[r.quantity for r in trading.placed]}"
    )


def test_zero_quantity_forced_exit_blocks_new_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """强制离场可平量为 0 时必须 ERROR, 且当根不得开新仓"""
    strategy, trading, _, _ = build_strategy()
    seed_position(
        strategy,
        "POS-EMPTY",
        quantity=0.0,
        opening_price=0.5,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
    )

    with caplog.at_level(logging.ERROR):
        push_kline(strategy, 0.47)

    assert "可平量为 0" in caplog.text, "强制离场无单可下必须 ERROR"
    assert trading.placed == [], "可平量为 0 时不得下单, 也不得开新仓"


def test_config_hot_update_does_not_retroactively_enable_stop():
    """止损开关/费率是开仓快照: 热更新只影响新仓, 不回溯存量仓位"""
    strategy, _, _, _ = build_strategy(enable_order_stop_loss=False)
    push_kline(strategy, 0.5)
    old = strategy.positions[0]
    assert old.enable_stop_loss is False

    strategy.apply_config(
        strategy.config.model_copy(
            update={"enable_order_stop_loss": True, "order_stop_loss_rate": 0.01}
        )
    )

    assert old.enable_stop_loss is False, "存量仓位不得被热更新回溯开启止损"
    assert old.stop_loss_rate == 0.0

    push_kline(strategy, 0.49, timestamp=TIMESTAMP + 60_000)

    assert old.pending_exit_order is None, "存量仓位不因新配置获得止损"
    assert len(strategy.positions) == 2, "新层应按新配置开启止损建仓"
    new = strategy.positions[1]
    assert new.enable_stop_loss is True
    assert new.stop_loss_rate == pytest.approx(0.01)