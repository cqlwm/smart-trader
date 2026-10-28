"""只读查询构造（SQLAlchemy Core SELECT）

写仓储（`OrderJournal`）与只读仓储（`OrderJournalReader`）共用同一份查询语义,
避免"读路径复制一份 SQL"的第二真相。所有函数只接收连接, 不管理事务。
"""

from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection

from smart_trader.model import OrderStatus
from smart_trader.storage import schema
from smart_trader.storage.records import GridPositionRow, OrderRecord


def select_positions(
    conn: Connection,
    *,
    strategy_key: str | None,
    include_closed: bool,
) -> list[GridPositionRow]:
    """仓位行: 可选策略键过滤; 默认只取 active=1, 按写入序"""
    stmt = select(schema.grid_positions)
    if strategy_key is not None:
        stmt = stmt.where(schema.grid_positions.c.strategy_key == strategy_key)
    if not include_closed:
        stmt = stmt.where(schema.grid_positions.c.active == 1)
    stmt = stmt.order_by(text("rowid"))
    rows = conn.execute(stmt).mappings().all()
    return [GridPositionRow.model_validate(dict(row)) for row in rows]


def select_positions_by_ids(
    conn: Connection, position_ids: list[str]
) -> list[GridPositionRow]:
    """按仓位号加载（含已归档）; 空列表直接返回 []"""
    if not position_ids:
        return []
    stmt = (
        select(schema.grid_positions)
        .where(schema.grid_positions.c.id.in_(position_ids))
        .order_by(text("rowid"))
    )
    rows = conn.execute(stmt).mappings().all()
    return [GridPositionRow.model_validate(dict(row)) for row in rows]


def select_orders_for_positions(
    conn: Connection, position_ids: list[str]
) -> list[OrderRecord]:
    """若干逻辑仓位的全部订单行（按 created_at, rowid 排序）"""
    if not position_ids:
        return []
    stmt = (
        select(schema.orders)
        .where(schema.orders.c.grid_position_id.in_(position_ids))
        .order_by(schema.orders.c.created_at, text("rowid"))
    )
    rows = conn.execute(stmt).mappings().all()
    return [OrderRecord.model_validate(dict(row)) for row in rows]


def select_orders(
    conn: Connection,
    *,
    strategy_key: str | None,
    status: OrderStatus | None,
    unconfirmed: bool,
    limit: int,
    offset: int,
) -> list[OrderRecord]:
    """订单分页（创建时间倒序）"""
    stmt = select(schema.orders)
    if strategy_key is not None:
        stmt = stmt.where(schema.orders.c.strategy_key == strategy_key)
    if unconfirmed:
        stmt = stmt.where(schema.orders.c.status.is_(None))
    elif status is not None:
        stmt = stmt.where(schema.orders.c.status == status.value)
    stmt = (
        stmt.order_by(schema.orders.c.created_at.desc(), text("rowid DESC"))
        .limit(limit)
        .offset(offset)
    )
    rows = conn.execute(stmt).mappings().all()
    return [OrderRecord.model_validate(dict(row)) for row in rows]


def count_orders(
    conn: Connection,
    *,
    strategy_key: str | None,
    status: OrderStatus | None,
    unconfirmed: bool,
) -> int:
    """与 select_orders 同口径的计数"""
    stmt = select(func.count()).select_from(schema.orders)
    if strategy_key is not None:
        stmt = stmt.where(schema.orders.c.strategy_key == strategy_key)
    if unconfirmed:
        stmt = stmt.where(schema.orders.c.status.is_(None))
    elif status is not None:
        stmt = stmt.where(schema.orders.c.status == status.value)
    total = conn.execute(stmt).scalar_one()
    return int(total)


def select_pending_orders(
    conn: Connection, *, strategy_key: str | None = None
) -> list[OrderRecord]:
    """结果未确认（status IS NULL）的订单, 即待对账清单"""
    stmt = select(schema.orders).where(schema.orders.c.status.is_(None))
    if strategy_key is not None:
        stmt = stmt.where(schema.orders.c.strategy_key == strategy_key)
    stmt = stmt.order_by(schema.orders.c.created_at, text("rowid"))
    rows = conn.execute(stmt).mappings().all()
    return [OrderRecord.model_validate(dict(row)) for row in rows]


def select_strategy_keys(conn: Connection) -> list[str]:
    """账本中出现过的全部策略键（活跃 + 归档）"""
    stmt = (
        select(schema.grid_positions.c.strategy_key)
        .distinct()
        .order_by(schema.grid_positions.c.strategy_key)
    )
    return [str(row[0]) for row in conn.execute(stmt).all()]
