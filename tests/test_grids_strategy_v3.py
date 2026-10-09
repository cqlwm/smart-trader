"""grids_strategy_v3 与 grids_strategy_v2 的行为一致性(重构不变式)测试。

v3 的目标是"行为不变的重构", 因此本文件的主体是: 对同一组场景,
分别驱动 v2 与 v3 策略, 断言两者可观测结果完全一致。
另附若干 v3 新增结构(ProfitLevel / CloseResult)的直接单测。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import Mock

import pytest
from pandas import DataFrame

from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import Kline, OrderSide, Symbol
from smart_trader.strategy import Signal
from smart_trader.strategy import grids_strategy_v2 as v2
from smart_trader.strategy import grids_strategy_v3 as v3

TIMEFRAME = "1m"
SYMBOL = Symbol(base="BTC", quote="USDT")


class _StubSignal(Signal):
    """可控入场/退出信号的桩实现"""

    def __init__(self, side: OrderSide, *, entry: bool = False, exit_: bool = False):
        super().__init__(side)
        self._entry = entry
        self._exit = exit_

    def run(self, klines: DataFrame) -> int:
        return 0

    def is_entry(self, df: DataFrame) -> bool:
        return self._entry

    def is_exit(self, df: DataFrame) -> bool:
        return self._exit


_EXIT_SIGNAL = _StubSignal(OrderSide.BUY, entry=False, exit_=True)


def _build(config_cls: Any, strategy_cls: Any, overrides: dict[str, Any]) -> Any:
    ex_client = Mock(spec=ExSwapClient)
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "oid",
        "price": 0.0,
        "status": "closed",
    }
    kwargs: dict[str, Any] = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "order_file_path": "",
        **overrides,
    }
    return strategy_cls(config_cls(**kwargs), ex_client), ex_client


def _set_kline(
    strategy: Any, close: float, high: float | None = None, low: float | None = None
) -> None:
    strategy.kline_data_dict[TIMEFRAME].latest_kline = Kline(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=0.0,
        timestamp=0,
        finished=True,
    )


def _add_order(strategy: Any, order_cls: Any, **overrides: Any) -> None:
    base: dict[str, Any] = {
        "entry_id": "seed1",
        "side": OrderSide.BUY,
        "price": 100.0,
        "quantity": 100.0,
        "fixed_take_profit_rate": 0.01,
        "signal_min_take_profit_rate": 0.002,
        "status": "closed",
    }
    base.update(overrides)
    strategy.order_manager.add_order(order_cls(**base))


def _orders_snapshot(strategy: Any) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": order.entry_id,
            "side": order.side.value,
            "price": order.price,
            "quantity": order.quantity,
            "status": order.status,
            "exit_price": order.exit_price,
            "current_stop_price": order.current_stop_price,
            "enable_stop_loss": order.enable_stop_loss,
            "enable_trailing_stop": order.enable_trailing_stop,
        }
        for order in strategy.order_manager.orders
    ]


def _snapshot(strategy: Any, ex_client: Mock) -> dict[str, Any]:
    return {
        "orders": _orders_snapshot(strategy),
        "is_running": strategy.is_running,
        "close_position": strategy.close_position,
        # custom_id 由随机 token 生成, 比较时会不同, 故排除
        "place_calls": [
            {k: v for k, v in call.kwargs.items() if k != "custom_id"}
            for call in ex_client.place_order_v2.call_args_list
        ],
        "cancel_calls": [list(call.args) for call in ex_client.cancel.call_args_list],
        "query_calls": [list(call.args) for call in ex_client.query_order.call_args_list],
    }


def _run_kline_finished(strategy: Any) -> None:
    strategy._on_kline_finished()


def _run_close_order(strategy: Any) -> None:
    strategy.check_close_order()


@dataclass
class Scenario:
    name: str
    config: dict[str, Any]
    setup: Callable[[Any, Mock, Any], None]
    run: Callable[[Any], None] = _run_kline_finished


# --------------------------------------------------------------------- 场景


def _scenario_open(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "entry1",
        "price": 100.0,
        "status": "open",
    }
    _set_kline(strategy, 100.0)


def _scenario_stop_loss_priority(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 94.0,
        "status": "closed",
    }
    _add_order(
        strategy,
        order_cls,
        entry_id="buy1",
        stop_loss_rate=0.05,
        enable_stop_loss=True,
        current_stop_price=95.0,
    )
    _set_kline(strategy, 94.0)


def _scenario_fixed_take_profit(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 101.1,
        "status": "closed",
    }
    _add_order(strategy, order_cls, entry_id="t1")
    _set_kline(strategy, 101.1)


def _scenario_max_order_stop_all(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 100.0,
        "status": "closed",
    }
    _add_order(strategy, order_cls, entry_id="a")
    _add_order(strategy, order_cls, entry_id="b", price=101.0)
    _set_kline(strategy, 100.0)


def _scenario_trailing_stop(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    _add_order(
        strategy,
        order_cls,
        entry_id="t1",
        enable_trailing_stop=True,
        trailing_stop_rate=0.02,
        trailing_stop_activation_profit_rate=0.01,
        current_stop_price=98.0,
    )
    _set_kline(strategy, 102.0, high=102.0, low=102.0)


def _scenario_exit_signal(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 100.5,
        "status": "closed",
    }
    _add_order(strategy, order_cls, entry_id="s1")
    _set_kline(strategy, 100.5)


def _scenario_no_kline(strategy: Any, ex_client: Mock, order_cls: Any) -> None:
    pass


def _scenario_close_position_ratio(
    strategy: Any, ex_client: Mock, order_cls: Any
) -> None:
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 101.1,
        "status": "closed",
    }
    _add_order(strategy, order_cls, entry_id="t1")
    _add_order(strategy, order_cls, entry_id="t2", price=99.0, quantity=50.0)
    _set_kline(strategy, 101.1)


SCENARIOS = [
    Scenario("open", {}, _scenario_open),
    Scenario(
        "stop_loss_priority",
        {
            "enable_order_stop_loss": True,
            "order_stop_loss_rate": 0.05,
            "grid_spacing_rate": 0.01,
        },
        _scenario_stop_loss_priority,
    ),
    Scenario(
        "fixed_take_profit",
        {"fixed_rate_take_profit": True, "fixed_take_profit_rate": 0.01},
        _scenario_fixed_take_profit,
    ),
    Scenario(
        "max_order_stop_all",
        {"enable_max_order_stop_loss": True, "max_order": 3},
        _scenario_max_order_stop_all,
    ),
    Scenario(
        "trailing_stop",
        {
            "enable_trailing_stop": True,
            "trailing_stop_rate": 0.02,
            "trailing_stop_activation_profit_rate": 0.01,
        },
        _scenario_trailing_stop,
    ),
    Scenario(
        "exit_signal",
        {"enable_exit_signal": True, "signal": _EXIT_SIGNAL},
        _scenario_exit_signal,
    ),
    Scenario("no_kline", {}, _scenario_no_kline),
    Scenario(
        "close_position_ratio",
        {
            "fixed_rate_take_profit": True,
            "fixed_take_profit_rate": 0.01,
            "close_position_ratio": 0.95,
        },
        _scenario_close_position_ratio,
        run=_run_close_order,
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_v3_behaviour_matches_v2(scenario: Scenario):
    """同一场景下 v3 的可观测行为必须与 v2 完全一致"""
    v2_strategy, v2_client = _build(v2.SignalGridStrategyConfig, v2.SignalGridStrategy, scenario.config)
    v3_strategy, v3_client = _build(v3.SignalGridStrategyConfig, v3.SignalGridStrategy, scenario.config)

    scenario.setup(v2_strategy, v2_client, v2.Order)
    scenario.setup(v3_strategy, v3_client, v3.Order)

    scenario.run(v2_strategy)
    scenario.run(v3_strategy)

    assert _snapshot(v3_strategy, v3_client) == _snapshot(v2_strategy, v2_client), (
        f"场景 {scenario.name}: v3 行为与 v2 不一致"
    )


# ------------------------------------------------------------ v3 新增结构单测


def test_profit_level_returns_enum():
    """profit_level 使用 ProfitLevel 枚举而非魔法数字"""
    order = v3.Order(
        entry_id="p1",
        side=OrderSide.BUY,
        price=100.0,
        quantity=1.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status="closed",
    )

    assert order.profit_level(101.5) is v3.ProfitLevel.TAKE_PROFIT
    assert order.profit_level(100.5) is v3.ProfitLevel.PROFIT
    # 价格刚好等于入场价时, 严格大于判定不成立, 仍属亏损
    assert order.profit_level(100.1) is v3.ProfitLevel.BREAK_EVEN
    assert order.profit_level(100.0) is v3.ProfitLevel.LOSS
    assert order.profit_level(99.0) is v3.ProfitLevel.LOSS


def test_close_result_reports_exit_orders():
    """触发平仓的订单进入 exit_orders"""
    strategy, ex_client = _build(
        v3.SignalGridStrategyConfig,
        v3.SignalGridStrategy,
        {"fixed_rate_take_profit": True, "fixed_take_profit_rate": 0.01},
    )
    ex_client.place_order_v2.return_value = {
        "clientOrderId": "exit1",
        "price": 101.1,
        "status": "closed",
    }
    _add_order(strategy, v3.Order, entry_id="t1")
    _set_kline(strategy, 101.1)

    result = strategy.check_close_order()

    assert isinstance(result, v3.CloseResult)
    assert len(result.exit_orders) == 1
    assert result.removed_orders == []
    assert result.exit_orders[0].exit_price == 101.1


def test_close_result_reports_removed_unfilled_orders():
    """未成交的入场单被同步为过期后进入 removed_orders, 而非 exit_orders"""
    strategy, ex_client = _build(
        v3.SignalGridStrategyConfig,
        v3.SignalGridStrategy,
        {"enable_order_stop_loss": True, "order_stop_loss_rate": 0.05},
    )
    ex_client.query_order.return_value = None
    _add_order(
        strategy,
        v3.Order,
        entry_id="u1",
        status="open",
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        current_stop_price=95.0,
    )
    _set_kline(strategy, 94.0)

    result = strategy.check_close_order()

    assert result.exit_orders == []
    assert len(result.removed_orders) == 1
    assert result.removed_orders[0].status == "expired"
    assert result.all_orders == result.removed_orders