"""GridPositionManager（逻辑仓位管理器）测试

覆盖: 内存与账本的同步语义, 惰性落账, 平仓归档, 重启恢复。
"""

from smart_trader.model import OrderSide, OrderStatus
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    GridPositionManager,
)
from tests.fakes import DEFAULT_STRATEGY_KEY, make_position_row

STRATEGY_KEY = DEFAULT_STRATEGY_KEY


def build_position(
    entry_id: str, status: OrderStatus = OrderStatus.OPEN
) -> GridPosition:
    return GridPosition(
        entry_id=entry_id,
        side=OrderSide.BUY,
        price=0.5,
        quantity=100.0,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=status,
    )


def seed_journal(journal: OrderJournal, entry_id: str = "e1") -> None:
    """把一行订单直接放进账本（模拟上次运行留下的状态）"""
    journal.sync_positions(
        STRATEGY_KEY, [make_position_row(entry_id)], removed_ids=[]
    )


class TestLoadPositions:
    def test_force_load_restores_orders_from_journal(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "e1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        assert manager.load_positions(force=True) is True

        assert [position.entry_id for position in manager.positions] == ["e1"]

    def test_load_without_force_is_noop(self):
        """运行期内存是权威状态, 非强制加载不做任何事"""
        journal = OrderJournal(":memory:")
        seed_journal(journal, "e1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        assert manager.load_positions() is False
        assert manager.positions == [], "非强制加载不得覆盖内存"


class TestRecordPositions:
    def test_record_writes_current_snapshot(self):
        journal = OrderJournal(":memory:")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)
        manager.add_position(build_position("e1"))

        manager.record_positions(refresh_orders=True)

        rows = journal.load_positions(STRATEGY_KEY)
        assert [row.entry_id for row in rows] == ["e1"]

    def test_record_is_lazy_without_changes(self):
        """没有状态变化时不落账; 显式 refresh 后才同步"""
        journal = OrderJournal(":memory:")
        seed_journal(journal, "e1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)
        manager.load_positions(force=True)

        manager.positions[0].filled_quantity = 50.0
        manager.record_positions()
        assert journal.load_positions(STRATEGY_KEY)[0].filled_quantity == 0.0, (
            "未声明确认变化时不得写账"
        )

        manager.record_positions(refresh_orders=True)
        assert journal.load_positions(STRATEGY_KEY)[0].filled_quantity == 50.0

    def test_closed_orders_are_removed_and_archived(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "e1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)
        manager.load_positions(force=True)
        closed = build_position("e1", status=OrderStatus.CLOSED)

        manager.record_positions([closed])

        assert manager.positions == [], "已平仓订单不应留在当前持仓"
        assert journal.load_positions(STRATEGY_KEY) == [], "账本中不再作为活跃订单加载"
