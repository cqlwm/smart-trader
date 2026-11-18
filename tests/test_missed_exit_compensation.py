"""停机信号补偿器测试: 回放窗口 / 最早离场点 / 信号状态同步 / 边界"""

import pytest

from smart_trader.model import (
    Kline,
    OrderSide,
    OrderStatus,
    SignalDirection,
    SignalKind,
)
from smart_trader.strategy.exit_policy import (
    ExitPolicyChain,
    LayeredReducePolicy,
    SignalExitPolicy,
)
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.missed_exit_compensation import (
    MissedExit,
    MissedExitCompensator,
    MissedExitPlan,
)
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig
from tests.fakes import (
    DEFAULT_SYMBOL,
    FakeMarketDataPort,
    FakeSignal,
    make_kline,
    make_order_record,
    make_position,
)

START = 1_700_000_000_000
MINUTE = 60_000
STRATEGY_KEY = "compensation_long_1m"


def _config(signal: FakeSignal, **overrides: object) -> SignalGridStrategyConfig:
    fields: dict[str, object] = {
        "symbol": DEFAULT_SYMBOL,
        "timeframe": "1m",
        "strategy_key": STRATEGY_KEY,
        "signal": signal,
        "signal_kind": SignalKind.TRIGGER,
        "enable_signal_take_profit": False,
        "enable_signal_stop_loss": True,
        "enable_single_layer_signal_stop_loss": True,
    }
    fields.update(overrides)
    return SignalGridStrategyConfig.model_validate(fields)


def _position(entry_price: float = 100.0, quantity: float = 1.0) -> GridPosition:
    order = make_order_record(
        "buy1",
        "POS1",
        side=OrderSide.BUY,
        price=entry_price,
        quantity=quantity,
        filled_qty=quantity,
        avg_price=entry_price,
        status=OrderStatus.CLOSED,
    )
    return make_position("POS1", orders=[order])


def _klines(*closes: float) -> list[Kline]:
    return [
        make_kline(timestamp=START + index * MINUTE, close=close)
        for index, close in enumerate(closes)
    ]


def _stop_loss_chain() -> ExitPolicyChain:
    return ExitPolicyChain(
        [LayeredReducePolicy(SignalKind.TRIGGER, OrderSide.SELL, min_losing_layers=1)]
    )


def test_captures_earliest_exit_at_signal_kline_close():
    """多根缺口 K 线命中信号时, 取最早一根的收盘价与仓位"""
    klines = _klines(100.0, 100.0, 98.0, 97.0, 96.0)
    signal = FakeSignal(
        [SignalDirection.NEUTRAL, SignalDirection.SHORT, SignalDirection.SHORT]
    )
    compensator = MissedExitCompensator(
        _config(signal),
        FakeMarketDataPort(klines=klines),
        lambda: [_position()],
        _stop_loss_chain(),
        warmup_klines=2,
    )

    plan = compensator.plan(klines[1].timestamp)

    assert plan == MissedExitPlan(
        exits=[MissedExit(position_id="POS1", force=True)],
        price=97.0,
        signal_kline_ms=klines[3].timestamp,
        replayed_until_ms=klines[4].timestamp,
    )
    assert signal.evaluate_calls == 3, "全部缺口 K 线都要回放以同步信号去重状态"


def _no_positions() -> list[GridPosition]:
    return []


def test_no_positions_skips_fetch():
    market = FakeMarketDataPort(klines=_klines(100.0, 98.0))
    compensator = MissedExitCompensator(
        _config(FakeSignal([SignalDirection.SHORT])),
        market,
        _no_positions,
        _stop_loss_chain(),
    )

    plan = compensator.plan(START)

    assert plan == MissedExitPlan()
    assert market.ohlcv_calls == 0, "无持仓时不应请求行情"


def test_no_gap_returns_empty_plan():
    klines = _klines(100.0, 99.0)
    compensator = MissedExitCompensator(
        _config(FakeSignal([SignalDirection.SHORT])),
        FakeMarketDataPort(klines=klines),
        lambda: [_position()],
        _stop_loss_chain(),
        warmup_klines=2,
    )

    plan = compensator.plan(klines[-1].timestamp)

    assert plan == MissedExitPlan()


def test_gap_longer_than_window_is_skipped():
    """缺口超过补偿窗口时无法保证最早信号仍在窗口内, 必须放弃"""
    klines = _klines(*([100.0] * 2), *([99.0] * 10))
    signal = FakeSignal([SignalDirection.SHORT])
    compensator = MissedExitCompensator(
        _config(signal),
        FakeMarketDataPort(klines=klines),
        lambda: [_position()],
        _stop_loss_chain(),
        warmup_klines=2,
        max_gap_klines=2,
    )

    plan = compensator.plan(klines[1].timestamp)

    assert plan == MissedExitPlan(), "窗口未覆盖完整缺口时不得补偿"
    assert signal.evaluate_calls == 0


def test_plan_propagates_take_profit_force_false():
    """信号止盈不强制: 已有出场挂单时由执行层保留原挂单"""
    klines = _klines(90.0, 100.0, 101.0)
    signal = FakeSignal([SignalDirection.NEUTRAL, SignalDirection.SHORT])
    policy = ExitPolicyChain([SignalExitPolicy(SignalKind.TRIGGER, OrderSide.SELL)])
    compensator = MissedExitCompensator(
        _config(
            signal,
            enable_signal_take_profit=True,
            enable_signal_stop_loss=False,
            enable_single_layer_signal_stop_loss=False,
        ),
        FakeMarketDataPort(klines=klines),
        lambda: [_position(entry_price=90.0)],
        policy,
        warmup_klines=1,
    )

    plan = compensator.plan(klines[0].timestamp)

    assert plan.price == pytest.approx(101.0)
    assert plan.exits == [MissedExit(position_id="POS1", force=False)]
