"""逻辑仓位（`grid_position`）测试

覆盖: 从账本还原仓位与关联订单 / 订单不串仓 / 只还原活跃仓位 / 全量还原含归档 /
按 strategy_key 隔离 / 已实现盈亏（多头、空头、部分平仓、接管持仓、未平仓、均价缺失）。
"""

import pytest

from smart_trader.model import (
    OrderSide,
    OrderStatus,
    PositionCloseReason,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import OrderRecord
from smart_trader.strategy.grid_position import (
    GridPosition,
    load_all_positions,
    load_positions,
)
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


def make_position(
    orders: list[OrderRecord], **overrides: object
) -> GridPosition:
    """构造逻辑仓位（不落库, 直接喂订单）"""
    return GridPosition.from_row(make_position_row("POS1", **overrides), orders)


def entry_and_exit(
    entry_price: float,
    exit_price: float,
    *,
    quantity: float = 100.0,
    exit_quantity: float | None = None,
    side: PositionSide = PositionSide.LONG,
) -> list[OrderRecord]:
    """构造一笔入场 + 一笔出场订单（默认全部成交）"""
    entry_side = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL
    return [
        make_order_record(
            "entry",
            side=entry_side,
            filled_qty=quantity,
            avg_price=entry_price,
        ),
        make_order_record(
            "exit",
            side=entry_side.reversal(),
            filled_qty=quantity if exit_quantity is None else exit_quantity,
            avg_price=exit_price,
        ),
    ]


# ---- 已实现盈亏 ----


def test_realized_pnl_of_closed_long_position():
    """多头全部平仓: (出场均价 - 入场均价) × 数量"""
    position = make_position(entry_and_exit(0.5, 0.6))

    assert position.realized_pnl == pytest.approx(10.0), "100 × (0.6 - 0.5) = 10"


def test_realized_pnl_only_counts_exited_quantity():
    """部分平仓只按出场成交量结算, 剩余持仓不计入已实现盈亏"""
    position = make_position(entry_and_exit(0.5, 0.6, exit_quantity=40.0))

    assert position.realized_pnl == pytest.approx(4.0), "40 × (0.6 - 0.5) = 4"
    assert position.quantity == pytest.approx(60.0), "剩余 60 仍为浮动持仓"


def test_realized_pnl_includes_opening_quantity():
    """接管持仓的 opening_quantity 计入成本基准"""
    position = make_position(
        [
            make_order_record(
                "exit", side=OrderSide.SELL, filled_qty=100.0, avg_price=0.4
            )
        ],
        opening_quantity=100.0,
        opening_price=0.5,
    )

    assert position.realized_pnl == pytest.approx(-10.0), "100 × (0.4 - 0.5) = -10"


def test_realized_pnl_of_short_position():
    """空头方向取反: (入场均价 - 出场均价) × 数量"""
    position = make_position(
        entry_and_exit(0.6, 0.5, side=PositionSide.SHORT),
        position_side=PositionSide.SHORT,
    )

    assert position.realized_pnl == pytest.approx(10.0), "100 × (0.6 - 0.5) = 10"


def test_realized_pnl_without_exit_is_zero():
    """只有入场成交时没有已实现盈亏"""
    position = make_position(entry_and_exit(0.5, 0.6, exit_quantity=0.0))

    assert position.realized_pnl == pytest.approx(0.0), "未出场则不产生已实现盈亏"


def test_realized_pnl_falls_back_to_order_price():
    """成交均价缺失时退回委托价, 与 price 派生口径一致"""
    position = make_position(
        [
            make_order_record(
                "entry", side=OrderSide.BUY, filled_qty=100.0, avg_price=0.5
            ),
            make_order_record(
                "exit",
                side=OrderSide.SELL,
                price=0.6,
                filled_qty=100.0,
                avg_price=None,
            ),
        ]
    )

    assert position.realized_pnl == pytest.approx(10.0), "avg_price 缺失时用 price"


# ---- 全量还原（含归档） ----


def test_load_all_positions_includes_archived():
    """全量还原包含已归档仓位及其订单（盈亏统计需要历史仓位）"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")
    seed_journal(journal, "POS2")
    journal.close_position("POS1", PositionCloseReason.EXIT_SETTLED)

    positions = load_all_positions(journal, STRATEGY_KEY)

    assert [position.id for position in positions] == ["POS1", "POS2"], (
        "全量还原必须包含已归档仓位"
    )
    assert positions[0].orders, "归档仓位的订单也必须还原"


def test_load_all_positions_isolated_by_strategy_key():
    """全量还原同样只取本策略名下的仓位"""
    journal = OrderJournal(":memory:")
    seed_journal(journal, "POS1")

    positions = load_all_positions(journal, "other_key")

    assert positions == []