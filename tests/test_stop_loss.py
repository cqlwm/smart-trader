"""SignalGridStrategy 止损相关测试

覆盖: 单笔止损触发平仓、未触发/未激活时不动作、跟踪止损上移、
空头方向的止损判定、已有出场挂单时不重复下单、暂停后仍跟踪出场单。
"""

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
from tests.fakes import DEFAULT_SYMBOL, make_order_record
from tests.test_signal_grid_strategy import (
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