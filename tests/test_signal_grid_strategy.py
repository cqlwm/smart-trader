from unittest.mock import Mock

from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import Kline, OrderSide, Symbol
from smart_trader.strategy.grids_strategy_v2 import (
    Order,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)

TIMEFRAME = "1m"


def set_latest_kline(strategy: SignalGridStrategy, close: float) -> None:
    strategy.kline_data_dict[TIMEFRAME].latest_kline = Kline(
        symbol=strategy.config.symbol,
        timeframe=TIMEFRAME,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        timestamp=0,
        finished=True,
    )


def build_order(entry_id: str, price: float, quantity: float) -> Order:
    return Order(
        entry_id=entry_id,
        side=OrderSide.BUY,
        price=price,
        quantity=quantity,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status="closed",
    )


def test_close_position_ratio():
    """测试平仓比例功能"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        close_position_ratio=0.99,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
    )
    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    strategy = SignalGridStrategy(config, ex_client)
    strategy.order_manager.add_order(build_order("test1", 100.0, 100.0))
    set_latest_kline(strategy, 101.1)

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    ex_client.place_order_v2.assert_called_once()
    # 平仓数量 = 原数量 * 平仓比例
    assert ex_client.place_order_v2.call_args[1]["quantity"] == 100.0 * 0.99


def test_close_position_ratio_default():
    """测试默认平仓比例为1.0"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
    )
    assert config.close_position_ratio == 1.0

    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    strategy = SignalGridStrategy(config, ex_client)
    strategy.order_manager.add_order(build_order("test1", 100.0, 100.0))
    set_latest_kline(strategy, 101.1)

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 1
    # 完全平仓
    assert ex_client.place_order_v2.call_args[1]["quantity"] == 100.0


def test_close_position_ratio_multiple_orders():
    """测试多个订单的平仓比例"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
        close_position_ratio=0.95,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
    )
    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {"clientOrderId": "exit1", "price": 101.1}
    strategy = SignalGridStrategy(config, ex_client)
    strategy.order_manager.add_order(build_order("test1", 100.0, 100.0))
    strategy.order_manager.add_order(build_order("test2", 99.0, 50.0))
    set_latest_kline(strategy, 101.1)

    flat_orders = strategy.check_close_order()

    assert len(flat_orders) == 2
    # 总数量 = 100 + 50 = 150, 平仓数量 = 150 * 0.95
    assert ex_client.place_order_v2.call_args[1]["quantity"] == 150.0 * 0.95


def test_open_order_allowed_when_no_close_in_kline():
    """无平仓的K线仍能正常开仓"""
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="BTC", quote="USDT"),
        timeframe=TIMEFRAME,
        per_order_qty=100,
        order_file_path="",
    )
    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "entry1",
        "price": 100.0,
        "status": "open",
    }
    strategy = SignalGridStrategy(config, ex_client)
    set_latest_kline(strategy, 100.0)

    strategy._on_kline_finished()

    assert len(strategy.order_manager.orders) == 1
    assert strategy.order_manager.orders[0].entry_id == "entry1"
