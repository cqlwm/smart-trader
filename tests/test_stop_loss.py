import pytest
from unittest.mock import Mock
import math
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig, Order
from model import Symbol, OrderSide, PlaceOrderBehavior
from client.ex_client import ExSwapClient


def test_order_stop_loss_buy():
    """测试买入订单的止损功能"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,  # 5% 止损
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 创建买入订单，价格100，止损价95
    strategy.orders = [
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
            current_stop_price=95.0
        )
    ]

    # 模拟价格跌到95以下
    strategy.last_kline = Mock()
    strategy.last_kline.close = 94.0  # 触发止损

    ex_client.query_order.return_value = {"status": "closed"}
    ex_client.place_order_v2.return_value = {"price": 94.0}

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    assert flat_orders[0].exit_price == 94.0


def test_order_stop_loss_sell():
    """测试卖出订单的止损功能"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        master_side=OrderSide.SELL,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 创建卖出订单，价格100，止损价105
    strategy.orders = [
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
            current_stop_price=105.0
        )
    ]

    # 模拟价格涨到105以上
    strategy.last_kline = Mock()
    strategy.last_kline.close = 106.0  # 触发止损

    ex_client.query_order.return_value = {"status": "closed"}
    ex_client.place_order_v2.return_value = {"price": 106.0}

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    assert flat_orders[0].exit_price == 106.0


def test_trailing_stop_buy():
    """测试买入订单的跟踪止损"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 创建买入订单，价格100，初始止损价98 (100 * (1 - 0.02))
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
        current_stop_price=98.0  # 初始止损价
    )
    strategy.orders = [order]

    # 价格涨到102 (超过激活价格101)，止损价应该更新到100 (102 * (1 - 0.02))
    strategy.last_kline = Mock()
    strategy.last_kline.close = 102.0

    strategy._update_trailing_stops(102.0)

    assert order.current_stop_price == pytest.approx(99.96, abs=1e-6)  # max(98, 102 * (1 - 0.02))


def test_trailing_stop_sell():
    """测试卖出订单的跟踪止损"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        master_side=OrderSide.SELL,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 创建卖出订单，价格100，初始止损价102 (100 * (1 + 0.02))
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
        current_stop_price=102.0
    )
    strategy.orders = [order]

    # 价格跌到98 (低于激活价格99)，止损价应该更新到100 (98 * (1 + 0.02))
    strategy.last_kline = Mock()
    strategy.last_kline.close = 98.0

    strategy._update_trailing_stops(98.0)

    assert order.current_stop_price == pytest.approx(99.96, abs=1e-6)  # min(102, 98 * (1 + 0.02))


def test_trailing_stop_not_activated():
    """测试跟踪止损未激活的情况"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 创建买入订单，价格100，激活价格101
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
        current_stop_price=98.0
    )
    strategy.orders = [order]

    # 价格只涨到100.5，未达到激活价格101，止损价不应该更新
    strategy.last_kline = Mock()
    strategy.last_kline.close = 100.5

    strategy._update_trailing_stops(100.5)

    assert order.current_stop_price == 98.0  # 保持不变


def test_order_initialization_with_stop_loss():
    """测试订单初始化时正确设置止损参数"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 模拟K线数据
    strategy.last_kline = Mock()
    strategy.last_kline.close = 100.0

    # 模拟下单成功
    ex_client.place_order_v2.return_value = {"clientOrderId": "test123", "price": 100.0, "status": "open"}

    # 触发开仓
    result = strategy.check_open_order()
    assert result == True

    # 检查订单参数
    order = strategy.orders[0]
    assert order.enable_stop_loss == True
    assert order.stop_loss_rate == 0.05
    assert order.enable_trailing_stop == True
    assert order.trailing_stop_rate == 0.02
    assert order.trailing_stop_activation_profit_rate == 0.01
    assert order.current_stop_price == 95.0  # 100 * (1 - 0.05)
