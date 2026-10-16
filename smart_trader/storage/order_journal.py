"""订单日志（sqlite, SQLAlchemy Core）

两张表:

- `grid_positions`: 逻辑仓位（GridPosition）的策略参数与运行时状态, 以 strategy_key 归属,
  `active` 标记当前/归档; 价格 / 数量 / 相位由 `orders` 派生, 不落库。
- `orders`: 订单流水, 先落行再发交易所; `status` 为空 = 结果未确认,
  承担 WAL 标记与对账线索（原 `order_ids` 表已删除）。

实现约定（设计见 .memory/03-coding-design.md）:

- 表结构来自 `schema.py`（单一来源）, 记录模型来自 `records.py`;
- 语句全部用 SQLAlchemy Core 构造, 不手写 SQL 字符串;
- 每个方法通过 engine 独占一次连接与事务, 天然线程安全;
  文件库依赖 SQLite WAL + busy_timeout 串行化并发写, 内存库（测试）用 StaticPool 单连接。
"""

import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Engine,
    Table,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from smart_trader import log
from smart_trader.model import OrderSide, PositionCloseReason
from smart_trader.storage import schema
from smart_trader.storage.records import GridPositionRow, OrderRecord

logger = log.getLogger(__name__)

_MAX_ID_RETRY = 5


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    """每个物理连接建立时设置: WAL（内存库自动忽略）+ 写锁等待 30s"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def _create_engine(db_path: str) -> Engine:
    """创建 SQLite 引擎: 文件库用默认连接池, 内存库（测试）用 StaticPool 共享单连接"""
    if db_path == ":memory:":
        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
    event.listen(engine, "connect", _on_connect)
    return engine


class OrderJournal:
    """订单日志: 逻辑仓位表 + 订单流水表 + 事务, 线程安全

    schema.py 是"当前结构"的唯一声明, 代码不维护迁移历史: 打开缺列旧库时
    响亮报错并内联生成可执行的 ALTER 语句, 手动执行一次即完成结构演化。
    """

    def __init__(self, db_path: str = ":memory:"):
        self._engine = _create_engine(db_path)
        with self._engine.begin() as conn:
            schema.metadata.create_all(conn)
            self._require_current_schema(conn)

    @staticmethod
    def _require_current_schema(conn: Connection) -> None:
        """打开即校验库结构与 schema 一致; 缺列时报错内联可执行的 ALTER 语句"""
        existing = {
            str(row[1])
            for row in conn.execute(text("PRAGMA table_info(grid_positions)"))
        }
        missing = [
            column
            for column in schema.grid_positions.columns
            if column.name not in existing
        ]
        if missing:
            statements = "; ".join(
                f"ALTER TABLE grid_positions ADD COLUMN {column.name} "
                f"{column.type.compile(sqlite_dialect())}"
                for column in missing
            )
            raise ValueError(
                f"账本库结构过时, grid_positions 缺少列 "
                f"{[column.name for column in missing]}; "
                f"请在 sqlite3 中执行: {statements}"
                f"（或执行 scripts/migrations/ 下对应编号脚本）"
            )

    def close(self) -> None:
        self._engine.dispose()

    # ---- id 生成 ----

    @staticmethod
    def generate_position_id() -> str:
        """生成逻辑仓位号（首单与仓位行同事务写入）"""
        return f"POS{secrets.token_hex(nbytes=5)}"

    @staticmethod
    def generate_order_id(side: OrderSide) -> str:
        """生成订单号（custom_id）"""
        return f"{side.value}{secrets.token_hex(nbytes=5)}"

    # ---- 写 ----

    def open_position(
        self, position: GridPositionRow, order: OrderRecord
    ) -> tuple[str, str]:
        """
        同一事务插入仓位行与首单行, 避免"有仓位无订单"/"有订单无仓位"的孤儿

        @param position: 仓位行模板; `id` 为空则生成
        @param order: 首单行; `order_id` 为空则生成, `grid_position_id` 以仓位号为准
        @return: (position_id, order_id)
        """
        for _ in range(_MAX_ID_RETRY):
            position_id = position.id or self.generate_position_id()
            order_id = order.order_id or self.generate_order_id(order.side)
            row = position.model_copy(update={"id": position_id})
            record = order.model_copy(
                update={"order_id": order_id, "grid_position_id": position_id}
            )
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        insert(schema.grid_positions).values(**self._values(row))
                    )
                    conn.execute(insert(schema.orders).values(**self._values(record)))
            except IntegrityError:
                continue
            return position_id, order_id
        raise RuntimeError(f"连续 {_MAX_ID_RETRY} 次未能生成唯一仓位/订单号")

    def append_order(self, order: OrderRecord) -> str:
        """
        插入一条订单行（下单前预写, `status` 为空）; `order_id` 为空则生成

        @return: 实际写入的 order_id
        """
        for _ in range(_MAX_ID_RETRY):
            order_id = order.order_id or self.generate_order_id(order.side)
            record = order.model_copy(update={"order_id": order_id})
            try:
                with self._engine.begin() as conn:
                    conn.execute(insert(schema.orders).values(**self._values(record)))
            except IntegrityError:
                continue
            return order_id
        raise RuntimeError(f"连续 {_MAX_ID_RETRY} 次未能生成唯一订单号")

    def settle_order(self, order: OrderRecord) -> None:
        """按 order_id 回写 price/quantity/filled_qty/avg_price/status"""
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.orders)
                .where(schema.orders.c.order_id == order.order_id)
                .values(
                    price=order.price,
                    quantity=order.quantity,
                    filled_qty=order.filled_qty,
                    avg_price=order.avg_price,
                    status=order.status.value if order.status is not None else None,
                    updated_at=_now(),
                )
            )

    def discard_order(self, order_id: str) -> None:
        """删除预写行（交易所从未存在过该订单）, 该 order_id 可复用"""
        with self._engine.begin() as conn:
            conn.execute(delete(schema.orders).where(schema.orders.c.order_id == order_id))

    def upsert_position(self, position: GridPositionRow) -> None:
        """写 / 更新仓位行（如 trailing_stop_price 变化）"""
        with self._engine.begin() as conn:
            self._upsert(conn, schema.grid_positions, "id", position)

    def close_position(self, position_id: str, reason: PositionCloseReason) -> None:
        """归档仓位: 保留行, `active=0` 与归档原因同语句写入（审计可查）"""
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.grid_positions)
                .where(schema.grid_positions.c.id == position_id)
                .values(active=0, closed_reason=reason.value, updated_at=_now())
            )

    # ---- 读 ----

    def load_active_positions(self, strategy_key: str) -> list[GridPositionRow]:
        """加载策略当前仓位（active=1, 按写入序）"""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.grid_positions)
                    .where(
                        schema.grid_positions.c.strategy_key == strategy_key,
                        schema.grid_positions.c.active == 1,
                    )
                    .order_by(text("rowid"))
                )
                .mappings()
                .all()
            )
        return [GridPositionRow.model_validate(dict(row)) for row in rows]

    def load_orders_for_positions(self, position_ids: list[str]) -> list[OrderRecord]:
        """加载若干逻辑仓位的全部订单行（按 created_at, rowid 排序; 空列表返回 []）"""
        if not position_ids:
            return []
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.orders)
                    .where(schema.orders.c.grid_position_id.in_(position_ids))
                    .order_by(schema.orders.c.created_at, text("rowid"))
                )
                .mappings()
                .all()
            )
        return [OrderRecord.model_validate(dict(row)) for row in rows]

    def pending_orders(self, strategy_key: str) -> list[OrderRecord]:
        """结果未确认（status IS NULL）的订单, 即待对账清单"""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.orders)
                    .where(
                        schema.orders.c.strategy_key == strategy_key,
                        schema.orders.c.status.is_(None),
                    )
                    .order_by(schema.orders.c.created_at, text("rowid"))
                )
                .mappings()
                .all()
            )
        return [OrderRecord.model_validate(dict(row)) for row in rows]

    # ---- 内部工具 ----

    @staticmethod
    def _values(record: BaseModel) -> dict[str, Any]:
        """记录 → 列值字典: created_at 首写兜底, updated_at 每次都刷新"""
        now = _now()
        data: dict[str, Any] = record.model_dump(mode="json")
        data["created_at"] = data["created_at"] or now
        data["updated_at"] = now
        return data

    @classmethod
    def _upsert(
        cls,
        conn: Connection,
        table: Table,
        primary_key: str,
        record: BaseModel,
    ) -> None:
        """通用 upsert: 列值取自记录（JSON 模式）; 冲突时更新除主键与 created_at 外的所有列"""
        data = cls._values(record)
        stmt = sqlite_insert(table).values(**data)
        set_: dict[str, Any] = {
            name: stmt.excluded[name]
            for name in data
            if name not in (primary_key, "created_at")
        }
        conn.execute(stmt.on_conflict_do_update(index_elements=[primary_key], set_=set_))