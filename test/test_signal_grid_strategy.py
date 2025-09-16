import pytest
from unittest.mock import Mock, patch
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig, Order
from model import Symbol, OrderSide, Kline
from client.ex_client import ExSwapClient


def test_close_position_ratio():
    """测试平仓比例功能"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        close_position_ratio=0.99,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 模拟订单和当前价格
    strategy.orders = [
        Order(
            custom_id="test1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed"
        )
    ]

    # 模拟当前价格达到盈利标准
    strategy.last_kline = Mock()
    strategy.last_kline.close = 101.1  # 确保超过fixed_take_profit_rate标准

    # 模拟交易所查询订单返回已关闭
    ex_client.query_order.return_value = {"status": "closed"}

    # 模拟下单返回
    ex_client.place_order_v2.return_value = {"price": 101.1}

    # 执行检查平仓
    flat_orders = strategy.check_close_order()

    # 验证结果
    assert len(flat_orders) == 1
    ex_client.place_order_v2.assert_called_once()

    # 验证平仓数量 = 原数量 * 平仓比例
    call_args = ex_client.place_order_v2.call_args
    assert call_args[1]['quantity'] == 100.0 * 0.99  # 99.0


def test_close_position_ratio_default():
    """测试默认平仓比例为1.0"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        per_order_qty=100,
    )

    assert config.close_position_ratio == 1.0

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    strategy.orders = [
        Order(
            custom_id="test1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed"
        )
    ]

    strategy.last_kline = Mock()
    strategy.last_kline.close = 101.1

    ex_client.query_order.return_value = {"status": "closed"}
    ex_client.place_order_v2.return_value = {"price": 101.1}

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    call_args = ex_client.place_order_v2.call_args
    assert call_args[1]['quantity'] == 100.0  # 完全平仓


def test_close_position_ratio_multiple_orders():
    """测试多个订单的平仓比例"""
    symbol = Symbol(base="BTC", quote="USDT")
    config = SignalGridStrategyConfig(
        symbol=symbol,
        close_position_ratio=0.95,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.01,
        per_order_qty=100,
    )

    ex_client = Mock(spec=ExSwapClient)
    strategy = SignalGridStrategy(config, ex_client)

    # 多个盈利订单
    strategy.orders = [
        Order(
            custom_id="test1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed"
        ),
        Order(
            custom_id="test2",
            side=OrderSide.BUY,
            price=99.0,
            quantity=50.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status="closed"
        )
    ]

    strategy.last_kline = Mock()
    strategy.last_kline.close = 101.1

    ex_client.query_order.return_value = {"status": "closed"}
    ex_client.place_order_v2.return_value = {"price": 101.1}

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 2
    call_args = ex_client.place_order_v2.call_args
    # 总数量 = 100 + 50 = 150, 平仓数量 = 150 * 0.95 = 142.5
    assert call_args[1]['quantity'] == 150.0 * 0.95