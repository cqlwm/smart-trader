"""PositionLifecycle 入场单成交结算（settle_entry_fills）单元测试

设计见 .memory/03-coding-design.md 三.12（Step 21）: 盘中路径每 tick 查询
入场单并回写成交事实 —— 只 settle 不撤单, 撤销未成交入场单属于出场流程
（settle_pending_entries）。查询容错语义与出场单跟踪同源:
异常跳过下一根再试; 查询不到按"距创建过近先跳过, 足够老才 EXPIRED"。
"""

from datetime import UTC, datetime, timedelta

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import OrderResult, OrderSide, OrderStatus, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import OrderRecord
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.position_lifecycle import PositionLifecycle
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig
from tests.fakes import (
    DEFAULT_SYMBOL,
    FakeTradingPort,
    make_order_record,
    make_position_row,
)

CONFIG = SignalGridStrategyConfig(
    symbol=DEFAULT_SYMBOL,
    timeframe="1m",
    strategy_key="lifecycle_long_buy_1m",
    per_order_qty=100.0,
    fixed_rate_take_profit=True,
    fixed_take_profit_rate=0.01,
)

FRESH_CREATED_AT = datetime.now(UTC).isoformat()
STALE_CREATED_AT = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()


def exchange_result(
    order_id: str, status: OrderStatus, filled_qty: float
) -> OrderResult:
    """构造交易所查询回报（100 DOGE @ 0.5 的入场单）"""
    return OrderResult(
        custom_id=order_id,
        symbol=Symbol.from_ccxt(DEFAULT_SYMBOL.ccxt()),
        side=OrderSide.BUY,
        status=status,
        quantity=100.0,
        filled_qty=filled_qty,
        price=0.5,
        avg_price=0.5 if filled_qty > 0 else None,
    )


def build_lifecycle(
    trading: FakeTradingPort, journal: OrderJournal
) -> PositionLifecycle:
    return PositionLifecycle(
        config=CONFIG,
        trading=trading,
        journal=journal,
        positions=list,
    )


def make_pending_position(
    journal: OrderJournal, *, created_at: str = ""
) -> GridPosition:
    """经真实账本预写一条 OPEN 入场单, 返回内存仓位对象（记录含账本时间戳）"""
    row = make_position_row()
    order = make_order_record(grid_position_id="", created_at=created_at)
    position_id, _ = journal.open_position(row, order)
    records = journal.load_orders_for_positions([position_id])
    assert len(records) == 1, f"应恰好预写一条订单行, got {len(records)}"
    return GridPosition.from_row(row.model_copy(update={"id": position_id}), records)


def ledger_order(
    journal: OrderJournal, position_id: str, order_id: str
) -> OrderRecord:
    """从账本读回指定订单行（模拟"另一个进程看到的磁盘状态"）"""
    records = [
        record
        for record in journal.load_orders_for_positions([position_id])
        if record.order_id == order_id
    ]
    assert len(records) == 1, f"账本应恰有一条 {order_id} 行, got {len(records)}"
    return records[0]


class TestSettleEntryFills:
    """盘中入场单成交结算: 只回写事实, 不撤单"""

    def test_settle_entry_fill_writes_closed_fill_to_ledger(self) -> None:
        """成交回报(CLOSED)回写账本与内存, 仓位数量随之派生, 且绝不撤单"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(exchange_result(order_id, OrderStatus.CLOSED, 100.0))
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.CLOSED, (
            f"内存记录应结算为 closed, got {settled.status}"
        )
        assert settled.filled_qty == 100.0, f"成交量应回写, got {settled.filled_qty}"
        assert settled.avg_price == 0.5, f"成交均价应回写, got {settled.avg_price}"
        assert position.quantity == 100.0, (
            f"仓位派生数量应为 100.0, got {position.quantity}"
        )
        row = ledger_order(journal, position.id, order_id)
        assert row.status is OrderStatus.CLOSED, (
            f"账本行应结算为 closed, got {row.status}"
        )
        assert row.filled_qty == 100.0, f"账本成交量应回写, got {row.filled_qty}"
        assert trading.canceled == [], (
            f"盘中结算只 settle 不撤单, 实际撤了 {trading.canceled}"
        )

    def test_settle_entry_keeps_open_order_resting(self) -> None:
        """未成交(open)的入场单保持挂单状态, 不做任何破坏性动作"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(exchange_result(order_id, OrderStatus.OPEN, 0.0))
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.OPEN, (
            f"未成交入场单应保持 open, got {settled.status}"
        )
        assert trading.canceled == [], (
            f"未成交入场单不得被盘中路径撤销, 实际撤了 {trading.canceled}"
        )

    def test_settle_entry_records_partial_fill(self) -> None:
        """部分成交(open + filled 40/100)回写成交量, 数量派生为 40"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(exchange_result(order_id, OrderStatus.OPEN, 40.0))
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.OPEN, (
            f"部分成交仍是 open, got {settled.status}"
        )
        assert settled.filled_qty == 40.0, f"部分成交量应回写, got {settled.filled_qty}"
        assert position.quantity == 40.0, (
            f"仓位派生数量应为 40.0, got {position.quantity}"
        )

    def test_settle_entry_skips_too_fresh_missing_order(self) -> None:
        """查询不到且订单距创建过近: 跳过(防交易所 settle 延迟), 不按 EXPIRED 结算"""
        journal = OrderJournal()
        position = make_pending_position(journal, created_at=FRESH_CREATED_AT)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(None)
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.OPEN, (
            f"过近的查不到订单应保持 open 待下次查询, got {settled.status}"
        )
        row = ledger_order(journal, position.id, order_id)
        assert row.status is OrderStatus.OPEN, f"账本行应保持 open, got {row.status}"

    def test_settle_entry_skips_missing_order_with_unknown_age(self) -> None:
        """created_at 为空(运行中刚下的内存行)按"过近"保守跳过"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        position.orders = [make_order_record(order_id=order_id, created_at="")]
        trading = FakeTradingPort()
        trading.queue_query(None)
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        assert position.orders[0].status is OrderStatus.OPEN, (
            f"年龄未知的查不到订单应保守跳过保持 open, "
            f"got {position.orders[0].status}"
        )

    def test_settle_entry_expires_stale_missing_order(self) -> None:
        """查询不到且订单足够老: 按 EXPIRED 结算(交易所从未存在该单)"""
        journal = OrderJournal()
        position = make_pending_position(journal, created_at=STALE_CREATED_AT)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(None)
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.EXPIRED, (
            f"足够老的查不到订单应结算为 expired, got {settled.status}"
        )
        row = ledger_order(journal, position.id, order_id)
        assert row.status is OrderStatus.EXPIRED, (
            f"账本行应结算为 expired, got {row.status}"
        )

    def test_settle_entry_tolerates_query_failure_and_retries_next_tick(self) -> None:
        """查询异常不中断: 订单原样保留, 下一根再试即结算成功"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        trading = FakeTradingPort()
        trading.queue_query(SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "网络中断"))
        trading.queue_query(exchange_result(order_id, OrderStatus.CLOSED, 100.0))
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)  # 异常 tick
        assert position.orders[0].status is OrderStatus.OPEN, (
            f"查询失败的订单应保留 open, got {position.orders[0].status}"
        )

        lifecycle.settle_entry_fills(position)  # 下一根
        assert position.orders[0].status is OrderStatus.CLOSED, (
            f"下一根查询应结算成功, got {position.orders[0].status}"
        )

    def test_settle_entry_is_idempotent(self) -> None:
        """同一成交事实重复结算结果一致, 不产生额外副作用"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        closed_result = exchange_result(order_id, OrderStatus.CLOSED, 100.0)
        trading = FakeTradingPort()
        trading.queue_query(closed_result, closed_result)
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)
        lifecycle.settle_entry_fills(position)

        settled = position.orders[0]
        assert settled.status is OrderStatus.CLOSED, (
            f"重复结算后状态应保持 closed, got {settled.status}"
        )
        assert settled.filled_qty == 100.0, (
            f"重复结算后成交量应保持 100.0, got {settled.filled_qty}"
        )
        assert len(trading.canceled) == 0, "重复结算不得引入撤单副作用"

    def test_settle_entry_does_not_query_exit_orders(self) -> None:
        """结算只针对入场单: 同仓位的出场单不被查询、不被触碰"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        order_id = position.orders[0].order_id
        exit_record = make_order_record(
            order_id="SELLexit1",
            grid_position_id=position.id,
            side=OrderSide.SELL,
            status=OrderStatus.OPEN,
        )
        journal.append_order(exit_record)
        position.orders.append(exit_record)
        trading = FakeTradingPort()
        trading.queue_query(exchange_result(order_id, OrderStatus.CLOSED, 100.0))
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        assert trading.queries == [order_id], (
            f"只应查询入场单, 实际查询了 {trading.queries}"
        )
        exit_after = next(o for o in position.orders if o.side is OrderSide.SELL)
        assert exit_after.status is OrderStatus.OPEN, (
            f"出场单不应被入场结算触碰, got {exit_after.status}"
        )

    def test_settle_entry_noop_without_pending_orders(self) -> None:
        """无未了结入场单的仓位不触发任何查询"""
        journal = OrderJournal()
        position = make_pending_position(journal)
        position.orders = [
            position.orders[0].model_copy(update={"status": OrderStatus.CLOSED})
        ]
        trading = FakeTradingPort()
        lifecycle = build_lifecycle(trading, journal)

        lifecycle.settle_entry_fills(position)

        assert trading.queries == [], (
            f"无未了结入场单不应查询, 实际查询了 {trading.queries}"
        )
