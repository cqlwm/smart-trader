from unittest.mock import Mock

import pytest

from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import Kline, OrderSide, Symbol
from smart_trader.strategy.grids_strategy_v2 import (
    Order,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)

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


def build_strategy(config: SignalGridStrategyConfig) -> tuple[SignalGridStrategy, Mock]:
    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 0.0}
    return SignalGridStrategy(config, ex_client), ex_client


def test_order_stop_loss_buy():
    """测试买入订单的止损功能"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,  # 5% 止损
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 94.0}

    # 买入订单，价格100，止损价95
    strategy.order_manager.add_order(
        Order(
            entry_id="buy1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed",
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            current_stop_price=95.0,
        )
    )
    set_latest_kline(strategy, 94.0)  # 触发止损

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    assert flat_orders[0].exit_price == 94.0


def test_order_stop_loss_sell():
    """测试卖出订单的止损功能"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        master_side=OrderSide.SELL,
        per_order_qty=100,
        order_file_path="",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 106.0}

    # 卖出订单，价格100，止损价105
    strategy.order_manager.add_order(
        Order(
            entry_id="sell1",
            side=OrderSide.SELL,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed",
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            current_stop_price=105.0,
        )
    )
    set_latest_kline(strategy, 106.0)  # 触发止损

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    assert flat_orders[0].exit_price == 106.0


def test_trailing_stop_buy():
    """测试买入订单的跟踪止损"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 买入订单，价格100，初始止损价98
    order = Order(
        entry_id="buy1",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status="closed",
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=98.0,
    )
    strategy.order_manager.add_order(order)

    # 最高价涨到102(超过激活价格101)，止损价更新为 max(98, 102 * (1 - 0.02))
    set_latest_kline(strategy, 102.0, high=102.0)
    strategy._on_kline_finished()

    assert order.current_stop_price == pytest.approx(99.96, abs=1e-6)


def test_trailing_stop_sell():
    """测试卖出订单的跟踪止损"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        master_side=OrderSide.SELL,
        per_order_qty=100,
        order_file_path="",
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 卖出订单，价格100，初始止损价102
    order = Order(
        entry_id="sell1",
        side=OrderSide.SELL,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status="closed",
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=102.0,
    )
    strategy.order_manager.add_order(order)

    # 最低价跌到98(低于激活价格99)，止损价更新为 min(102, 98 * (1 + 0.02))
    set_latest_kline(strategy, 98.0, low=98.0)
    strategy._on_kline_finished()

    assert order.current_stop_price == pytest.approx(99.96, abs=1e-6)


def test_trailing_stop_not_activated():
    """测试跟踪止损未激活的情况"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, _ = build_strategy(config)

    # 买入订单，价格100，激活价格101
    order = Order(
        entry_id="buy1",
        side=OrderSide.BUY,
        price=100.0,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status="closed",
        trailing_stop_rate=0.02,
        enable_trailing_stop=True,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=98.0,
    )
    strategy.order_manager.add_order(order)

    # 最高价只到100.5，未达到激活价格101，止损价保持不变
    set_latest_kline(strategy, 100.5, high=100.5)
    strategy._on_kline_finished()

    assert order.current_stop_price == 98.0


def test_stop_loss_takes_priority_over_open_order():
    """止损优先: 同一根K线同时满足止损与补仓条件时, 先执行止损且不再开新仓"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        grid_spacing_rate=0.01,
    )
    strategy, ex_client = build_strategy(config)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 94.0}

    strategy.order_manager.add_order(
        Order(
            entry_id="buy1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed",
            stop_loss_rate=0.05,
            enable_stop_loss=True,
            current_stop_price=95.0,
        )
    )
    # 价格94: 既触发止损(<=95), 又满足补仓间距(100 -> 94, 跌幅6% > 1%)
    set_latest_kline(strategy, 94.0)

    strategy._on_kline_finished()

    # 只下了止损单, 且方向为反向
    ex_client.place_order_v2.assert_called_once()
    assert ex_client.place_order_v2.call_args[1]["order_side"] == OrderSide.SELL
    # 本根K线不再补新仓
    assert strategy.order_manager.orders == [], "止损所在K线不应再开新仓"


def test_order_initialization_with_stop_loss():
    """测试订单初始化时正确设置止损参数"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
    )
    strategy, ex_client = build_strategy(config)
    set_latest_kline(strategy, 100.0)
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "test123",
        "price": 100.0,
        "status": "open",
    }

    result = strategy.check_open_order()
    assert result is True

    order = strategy.order_manager.orders[0]
    assert order.enable_stop_loss is True
    assert order.stop_loss_rate == 0.05
    assert order.enable_trailing_stop is True
    assert order.trailing_stop_rate == 0.02
    assert order.trailing_stop_activation_profit_rate == 0.01
    assert order.current_stop_price == 95.0  # 100 * (1 - 0.05)
