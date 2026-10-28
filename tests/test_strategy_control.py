"""SignalGridStrategy 控制面测试: 暂停 / 恢复 / 暂停下全平 / 撤销入场挂单"""

from smart_trader.model import (
    OrderResult,
    OrderSide,
    OrderStatus,
    PositionCloseReason,
)
from smart_trader.strategy.grid_position import GridPosition
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    make_order_record,
    make_position_row,
)
from tests.test_signal_grid_strategy import (
    TIMESTAMP,
    build_strategy,
    push_kline,
    seed_position,
)


def _order_result(status: OrderStatus) -> OrderResult:
    return OrderResult(
        custom_id="buy1",
        symbol=DEFAULT_SYMBOL,
        side=OrderSide.BUY,
        status=status,
        quantity=10.0,
        filled_qty=0.0,
        price=0.5,
    )


def test_strategy_key_matches_config():
    strategy, _, _, _ = build_strategy()
    assert strategy.strategy_key == DEFAULT_STRATEGY_KEY


def test_pause_blocks_entries_until_resume():
    strategy, _, _, _ = build_strategy()

    strategy.pause()
    push_kline(strategy, 0.5)
    assert strategy.positions == [], "暂停后不得开新仓"

    strategy.resume()
    push_kline(strategy, 0.5, timestamp=TIMESTAMP + 60_000)
    assert len(strategy.positions) == 1, "恢复后应重新允许入场"


def test_close_all_executes_even_when_paused():
    strategy, trading, journal, _ = build_strategy()
    position = seed_position(strategy, entry_price=0.5, quantity=100.0)

    strategy.pause()
    strategy.request_close_all()
    push_kline(strategy, 0.5)

    assert strategy.close_position is False, "全平标志必须一次性消费"
    assert position.pending_exit_order is not None, "暂停状态也必须执行全平"
    assert trading.placed, "应挂出出场单"
    assert trading.placed[-1].side is OrderSide.SELL
    assert journal.load_active_positions(DEFAULT_STRATEGY_KEY), "挂单未成交前仓位仍活跃"


def test_cancel_pending_entries_cancels_and_archives_empty_position():
    strategy, trading, journal, _ = build_strategy()
    row = make_position_row("POS1")
    order = make_order_record(
        "buy1",
        "POS1",
        status=OrderStatus.OPEN,
        quantity=10.0,
        filled_qty=0.0,
    )
    position = GridPosition.from_row(row, [order])
    journal.upsert_position(row)
    journal.append_order(order)
    strategy.add_position(position)

    trading.queue_query(
        _order_result(OrderStatus.OPEN),
        _order_result(OrderStatus.CANCELED),
    )

    affected, archived = strategy.cancel_pending_entries()

    assert (affected, archived) == (1, 1)
    assert trading.canceled == ["buy1"], "必须撤掉未成交入场单"
    assert strategy.positions == [], "归零且无存活订单应移出内存"
    assert journal.load_active_positions(DEFAULT_STRATEGY_KEY) == []
    archived_rows = journal.load_all_positions(DEFAULT_STRATEGY_KEY)
    assert archived_rows[0].closed_reason is PositionCloseReason.ENTRY_DISCARDED


def test_cancel_pending_entries_is_noop_without_pending_entries():
    strategy, trading, _, _ = build_strategy()

    assert strategy.cancel_pending_entries() == (0, 0)
    assert trading.canceled == []
