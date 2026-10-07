import pytest

from smart_trader.model import (
    Kline,
    OrderResult,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    Symbol,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fakes import FakeMarketDataPort, FakeTradingPort

SYMBOL = Symbol(base="BTC", quote="USDT")

TIMEFRAME = "1m"


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


def build_strategy(
    config: SignalGridStrategyConfig,
) -> tuple[SignalGridStrategy, FakeTradingPort]:
    trading = FakeTradingPort(status=OrderStatus.OPEN, price=0.0)
    return (
        SignalGridStrategy(
            config,
            trading,
            FakeMarketDataPort(),
            OrderJournal(":memory:"),
            reconcile_min_age_seconds=0.0,
        ),
        trading,
    )


def test_order_stop_loss_buy():
    """测试买入订单的止损功能"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,  # 5% 止损
    )
    strategy, trading = build_strategy(config)
    trading.queue_place(
        OrderResult(
            custom_id="exit1",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=100.0,
            price=94.0,
        )
    )

    # 买入订单，价格100，止损价95
    strategy.position_manager.add_position(
        GridPosition(
            entry_id="buy1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status=OrderStatus.CLOSED,
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            current_stop_price=95.0,
        )
    )
    set_latest_kline(strategy, 94.0)  # 触发止损

    closed_positions = strategy.check_close_position()

    assert len(closed_positions) == 1
    assert closed_positions[0].exit_price == 94.0
    assert len(trading.placed) == 1
    exit_request = trading.placed[0]
    assert exit_request.side is OrderSide.SELL, "多头止损必须卖出平仓"
    assert exit_request.quantity == pytest.approx(100.0)


def test_order_stop_loss_sell():
    """测试卖出订单的止损功能"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        master_side=OrderSide.SELL,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    strategy, trading = build_strategy(config)
    trading.queue_place(
        OrderResult(
            custom_id="exit1",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=100.0,
            price=106.0,
        )
    )

    # 卖出订单，价格100，止损价105
    strategy.position_manager.add_position(
        GridPosition(
            entry_id="sell1",
            side=OrderSide.SELL,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status=OrderStatus.CLOSED,
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            current_stop_price=105.0,
        )
    )
    set_latest_kline(strategy, 106.0)  # 触发止损

    closed_positions = strategy.check_close_position()

    assert len(closed_positions) == 1
    assert closed_positions[0].exit_price == 106.0
    assert trading.placed[0].side is OrderSide.BUY, "空头止损必须买入平仓"


def test_trailing_stop_buy():
    """测试买入订单的跟踪止损"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 买入订单，价格100，初始止损价98
    position = GridPosition(
        entry_id="buy1",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=OrderStatus.CLOSED,
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=98.0,
    )
    strategy.position_manager.add_position(position)

    # 最高价涨到102(超过激活价格101)，止损价更新为 max(98, 102 * (1 - 0.02))
    set_latest_kline(strategy, 102.0, high=102.0)
    strategy._on_kline_finished()

    assert position.current_stop_price == pytest.approx(99.96, abs=1e-6)


def test_trailing_stop_sell():
    """测试卖出订单的跟踪止损"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        master_side=OrderSide.SELL,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 卖出订单，价格100，初始止损价102
    position = GridPosition(
        entry_id="sell1",
        side=OrderSide.SELL,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=OrderStatus.CLOSED,
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=102.0,
    )
    strategy.position_manager.add_position(position)

    # 最低价跌到98(低于激活价格99)，止损价更新为 min(102, 98 * (1 + 0.02))
    set_latest_kline(strategy, 98.0, low=98.0)
    strategy._on_kline_finished()

    assert position.current_stop_price == pytest.approx(99.96, abs=1e-6)


def test_trailing_stop_not_activated():
    """测试跟踪止损未激活的情况"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 买入订单，价格100，激活价格101
    position = GridPosition(
        entry_id="buy1",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=OrderStatus.CLOSED,
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=98.0,
    )
    strategy.position_manager.add_position(position)

    # 最高价只到100.5，未达到激活价格101，止损价保持不变
    set_latest_kline(strategy, 100.5, high=100.5)
    strategy._on_kline_finished()

    assert position.current_stop_price == 98.0


def test_order_initialization_with_stop_loss():
    """测试订单初始化时正确设置止损参数"""
    config = SignalGridStrategyConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        per_order_qty=100,
        strategy_key="test_stop_loss",
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, trading = build_strategy(config)
    set_latest_kline(strategy, 100.0)
    trading.queue_place(
        OrderResult(
            custom_id="test123",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            status=OrderStatus.OPEN,
            quantity=100.0,
            price=100.0,
        )
    )

    result = strategy.check_open_position()
    assert result is True

    position = strategy.position_manager.positions[0]
    assert position.enable_stop_loss is True
    assert position.stop_loss_rate == 0.05
    assert position.enable_trailing_stop is True
    assert position.trailing_stop_rate == 0.02
    assert position.trailing_stop_activation_profit_rate == 0.01
    assert position.current_stop_price == 95.0  # 100 * (1 - 0.05)
