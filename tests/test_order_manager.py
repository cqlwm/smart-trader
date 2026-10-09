"""GridPositionManager（逻辑仓位管理器）测试

覆盖: 从账本还原仓位与关联订单 / 派生量 / 惰性加载语义。
"""

import pytest

from smart_trader.model import OrderSide, OrderStatus
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import GridPositionManager
from tests.fakes import DEFAULT_STRATEGY_KEY, make_order_record, make_position_row

STRATEGY_KEY = DEFAULT_STRATEGY_KEY


def seed_journal(journal: OrderJournal, position_id: str = "POS1") -> None:
    """把一行仓位 + 一条入场单直接放进账本（模拟上次运行留下的状态）"""
    journal.upsert_position(make_position_row(position_id))
    journal.append_order(
        make_order_record(
            f"o-{position_id}",
            position_id,
            side=OrderSide.BUY,
            filled_qty=100.0,
            avg_price=0.5,
            status=OrderStatus.CLOSED,
        )
    )


class TestLoadPositions:
    def test_force_load_restores_positions_with_orders(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "POS1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        assert manager.load_positions(force=True) is True

        positions = manager.positions
        assert [position.id for position in positions] == ["POS1"]
        assert [order.order_id for order in positions[0].orders] == ["o-POS1"], (
            "关联订单必须随仓位一起还原"
        )
        assert positions[0].quantity == pytest.approx(100.0), "派生数量来自订单成交"
        assert positions[0].price == pytest.approx(0.5), "派生均价来自订单成交均价"

    def test_load_without_force_is_noop(self):
        """运行期内存是权威状态, 非强制加载不做任何事"""
        journal = OrderJournal(":memory:")
        seed_journal(journal, "POS1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        assert manager.load_positions() is False
        assert manager.positions == [], "非强制加载不得覆盖内存"

    def test_orders_are_associated_with_owning_position(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "POS1")
        seed_journal(journal, "POS2")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        manager.load_positions(force=True)

        by_id = {position.id: position for position in manager.positions}
        assert [order.order_id for order in by_id["POS1"].orders] == ["o-POS1"]
        assert [order.order_id for order in by_id["POS2"].orders] == ["o-POS2"]

    def test_loads_only_active_positions(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "POS1")
        journal.close_position("POS1")
        manager = GridPositionManager(journal=journal, strategy_key=STRATEGY_KEY)

        manager.load_positions(force=True)

        assert manager.positions == [], "已归档仓位不应加载"

    def test_isolated_by_strategy_key(self):
        journal = OrderJournal(":memory:")
        seed_journal(journal, "POS1")
        manager = GridPositionManager(journal=journal, strategy_key="other_key")

        manager.load_positions(force=True)

        assert manager.positions == []