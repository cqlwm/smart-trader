"""逻辑仓位还原（`grid_position.load_positions`）测试

覆盖: 从账本还原仓位与关联订单 / 订单不串仓 / 只还原活跃仓位 / 按 strategy_key 隔离。
"""

import pytest

from smart_trader.model import (
    OrderSide,
    OrderStatus,
    PositionCloseReason,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.grid_position import load_positions
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


def test_restores_positions_with_orders():
    """仓位与关联订单一起还原, 派生量立即可用"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")

    positions = load_positions(journal, STRATEGY_KEY)

    assert [position.id for position in positions] == ["POS1"]
    assert [order.order_id for order in positions[0].orders] == ["o-POS1"], (
        "关联订单必须随仓位一起还原"
    )
    assert positions[0].quantity == pytest.approx(100.0), "派生数量来自订单成交"
    assert positions[0].price == pytest.approx(0.5), "派生均价来自订单成交均价"


def test_orders_are_associated_with_owning_position():
    """订单只挂到自己的仓位下, 不串仓"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")
    seed_journal(journal, "POS2")

    positions = load_positions(journal, STRATEGY_KEY)

    by_id = {position.id: position for position in positions}
    assert [order.order_id for order in by_id["POS1"].orders] == ["o-POS1"]
    assert [order.order_id for order in by_id["POS2"].orders] == ["o-POS2"]


def test_loads_only_active_positions():
    """已归档仓位不还原"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")
    journal.close_position("POS1", PositionCloseReason.EXIT_SETTLED)

    positions = load_positions(journal, STRATEGY_KEY)

    assert positions == [], "已归档仓位不应加载"


def test_isolated_by_strategy_key():
    """只还原本策略名下的仓位"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")

    positions = load_positions(journal, "other_key")

    assert positions == []