"""SignalGridStrategy 完成 K 线主流程测试

覆盖: 开仓条件、平仓优先于开仓、达到上限止损、孤儿回收。
用 tests.fakes 的内存端口 + :memory: 账本, 不触网、不落盘。
"""

from datetime import UTC, datetime

import pytest

from smart_trader.model import (
    Kline,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeMarketDataPort,
    FakeTradingPort,
    make_order_record,
    make_position_row,
)

TIMEFRAME = "1m"
TIMESTAMP = 1_700_000_000_000


def build_strategy(
    *,
    journal: OrderJournal | None = None,
    **overrides: object,
) -> tuple[SignalGridStrategy, FakeTradingPort, OrderJournal]:
    """组装一个全内存的策略实例

    @param journal 注入已有账本(用于验证启动加载); 默认新建空账本
    """
    fields: dict[str, object] = {
        "symbol": DEFAULT_SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy_key": DEFAULT_STRATEGY_KEY,
        "per_order_qty": 100.0,
        "grid_spacing_rate": 0.01,
        # NORMAL 直接走 TradingPort.place, 避免追单器引入额外分支
        "place_order_behavior": PlaceOrderBehavior.NORMAL,
    }
    fields.update(overrides)
    config = SignalGridStrategyConfig.model_validate(fields)
    trading = FakeTradingPort()
    if journal is None:
        journal = OrderJournal(":memory:")
    strategy = SignalGridStrategy(config, trading, FakeMarketDataPort(), journal)
    return strategy, trading, journal


def push_kline(
    strategy: SignalGridStrategy,
    close: float,
    high: float | None = None,
    low: float | None = None,
    timestamp: int = TIMESTAMP,
) -> None:
    """推进一根已收盘的 K 线（走 run() 公开入口）

    @param timestamp 默认固定值; 需要推进 K 线时间时传入不同值
    """
    strategy.run(
        Kline(
            symbol=DEFAULT_SYMBOL,
            timeframe=TIMEFRAME,
            open=close,
            high=close if high is None else high,
            low=close if low is None else low,
            close=close,
            volume=0.0,
            timestamp=timestamp,
            finished=True,
        )
    )


def seed_position(
    strategy: SignalGridStrategy,
    position_id: str = "POS1",
    *,
    entry_price: float = 0.5,
    quantity: float = 100.0,
    **row_overrides: object,
) -> GridPosition:
    """放入一个逻辑仓位: 附带一条按 `quantity` 成交的入场单（内存 + 账本）"""
    row = make_position_row(position_id, **row_overrides)
    entry_side = (
        OrderSide.BUY if row.position_side == PositionSide.LONG else OrderSide.SELL
    )
    order = make_order_record(
        f"o-{position_id}",
        position_id,
        side=entry_side,
        price=entry_price,
        quantity=quantity,
        filled_qty=quantity,
        avg_price=entry_price,
        status=OrderStatus.CLOSED,
    )
    position = GridPosition.from_row(row, [order])
    strategy.journal.upsert_position(row)
    strategy.add_position(position)
    return position


# ------------------------------------------------------------------ 启动加载


def test_restores_positions_from_journal_on_startup():
    """构造时必须从账本恢复活跃仓位, 否则策略会忘记重启前的敞口"""
    journal = OrderJournal(":memory:")
    journal.upsert_position(make_position_row("POS9"))
    journal.append_order(
        make_order_record(
            "o-POS9",
            "POS9",
            side=OrderSide.BUY,
            quantity=10.0,
            filled_qty=10.0,
            avg_price=0.5,
            status=OrderStatus.CLOSED,
        )
    )

    strategy, _, _ = build_strategy(journal=journal)

    assert [position.id for position in strategy.positions] == ["POS9"]
    assert strategy.positions[0].quantity == pytest.approx(10.0), (
        "关联订单必须随仓位一起还原"
    )


# ------------------------------------------------------------------ 开仓条件


def test_opens_position_when_grid_spacing_reached():
    """空仓位时, 价格入区间即开仓"""
    strategy, trading, _ = build_strategy()

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_skips_open_when_price_inside_grid_spacing():
    """价格未走出网格间距时不开仓"""
    strategy, trading, _ = build_strategy()
    seed_position(strategy)

    push_kline(strategy, 0.5005)  # 0.1% < 1% 间距

    assert len(strategy.positions) == 1
    assert trading.placed == []


def test_skips_open_when_price_outside_allowed_range():
    """价格超出可交易区间时不开仓"""
    strategy, trading, _ = build_strategy(lowest_price=0.4, highest_price=0.6)

    push_kline(strategy, 0.7)

    assert strategy.positions == []
    assert trading.placed == []


def test_opens_when_kline_has_no_close_action():
    """本根 K 线没有平仓动作时, 开仓照常执行"""
    strategy, trading, _ = build_strategy()
    seed_position(strategy)  # 未启用止损

    push_kline(strategy, 0.47)  # 跌破 6%, 但不构成平仓条件

    assert len(strategy.positions) == 2
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_empty_position_uses_opening_price_as_grid_reference():
    """空仓位以 opening_price 作为网格基准（无成交时的参考价）"""
    strategy, trading, _ = build_strategy()
    position = seed_position(strategy, quantity=0.0, opening_price=0.5)

    assert position.price == pytest.approx(0.5)
    assert position.quantity == 0.0

    push_kline(strategy, 0.4995)  # 0.1% < 1% 间距

    assert len(strategy.positions) == 1
    assert trading.placed == []


# ------------------------------------------------------- 平仓优先于开仓（互斥）


def test_close_takes_priority_over_open_on_stop_loss():
    """止损触发时先平仓; 本根 K 线不得再开新仓"""
    strategy, trading, _ = build_strategy(
        enable_order_stop_loss=True, order_stop_loss_rate=0.05
    )
    seed_position(
        strategy,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        trailing_stop_price=0.475,
    )

    push_kline(strategy, 0.47)  # 跌破止损价, 同时满足补仓间距

    assert [request.side for request in trading.placed] == [OrderSide.SELL], (
        "本根 K 线只应挂出平仓单"
    )
    assert len(strategy.positions) == 1, "平仓当根不得再开新仓"
    assert strategy.positions[0].pending_exit_order is not None


def test_fixed_take_profit_places_exit_and_skips_open():
    """固定止盈触发时同样平仓优先"""
    strategy, trading, _ = build_strategy(
        fixed_rate_take_profit=True, fixed_take_profit_rate=0.01
    )
    seed_position(strategy)

    push_kline(strategy, 0.51)

    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert len(strategy.positions) == 1


# --------------------------------------------------------------- 达到上限止损


def test_stop_all_at_max_order_pauses_strategy():
    """达到上限: 全部挂平仓单 + 暂停策略"""
    strategy, trading, _ = build_strategy(enable_max_order_stop_loss=True, max_order=3)
    seed_position(strategy, "POS1", entry_price=0.5)
    seed_position(strategy, "POS2", entry_price=0.49)
    stop_calls: list[str] = []
    strategy.on_stop_loss_order_all = lambda: stop_calls.append("called")

    push_kline(strategy, 0.48)

    assert stop_calls == ["called"], "达到上限止损必须回调"
    assert strategy.is_running is False, "默认止损后暂停策略"
    assert [request.side for request in trading.placed] == [
        OrderSide.SELL,
        OrderSide.SELL,
    ]
    assert len(strategy.positions) == 2, "出场单未成交, 不归档"


def test_stop_all_without_pause_still_skips_open():
    """关闭暂停后 is_running 仍为真, 但本根 K 线已平仓 -> 不开新仓"""
    strategy, trading, _ = build_strategy(
        enable_max_order_stop_loss=True, max_order=2, paused_after_stop_loss=False
    )
    seed_position(strategy, "POS1", entry_price=0.5)

    push_kline(strategy, 0.47)

    assert strategy.is_running is True
    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert len(strategy.positions) == 1, "平仓当根不得再开新仓"


# ------------------------------------------------------------------ 孤儿回收


def test_reaps_expired_empty_position():
    """空仓位超过阈值时长后回收（归档）"""
    strategy, _, journal = build_strategy()
    row = make_position_row("POS9", created_at="2020-01-01T00:00:00+00:00")
    journal.upsert_position(row)
    strategy.add_position(GridPosition.from_row(row, []))

    push_kline(strategy, 0.5)

    assert strategy.positions == [], "超时空仓位应被回收"
    assert journal.load_active_positions(DEFAULT_STRATEGY_KEY) == [], (
        "回收即置 active=0"
    )


def test_keeps_recent_empty_position():
    """空仓位未超时则保留, 继续充当网格价格参考"""
    strategy, _, journal = build_strategy()
    row = make_position_row("POS9", created_at=datetime.now(UTC).isoformat())
    journal.upsert_position(row)
    strategy.add_position(GridPosition.from_row(row, []))

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1