"""订单日志（sqlite, SQLAlchemy Core）

三张表:

- `order_ids`（WAL）: 下单前先预留订单号（used=0 + 意图快照）,
  挂单成功后与仓位/流水同事务核销; 明确拒绝直接核销;
  结果未知保持 used=0, 由对账器去交易所确认。语义见 .memory/01-system-architecture.md 第十一节。
- `grid_positions`: 逻辑仓位（GridPosition）的持久化, 以 strategy_key 归属, active 标记当前/归档。
- `orders`: 订单流水, 一次"交易所确认存在过"的委托一行（append 为主, 被拒尝试只留在 id 表）。

实现约定（设计见 .memory/03-coding-design.md）:

- 表结构来自 `schema.py`（单一来源）, 记录模型来自 `records.py`;
- 语句全部用 SQLAlchemy Core 构造, 不手写 SQL 字符串
  （唯一例外是 `_migrate_legacy_schema`, 它面对未知的历史结构）;
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
    event,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from smart_trader import log
from smart_trader.model import OrderSide
from smart_trader.storage import schema
from smart_trader.storage.records import (
    GridPositionRow,
    IdRecord,
    OrderIntent,
    OrderRecord,
)

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
    """订单日志: id 表（WAL）+ 逻辑仓位表 + 订单流水表 + 事务, 线程安全"""

    def __init__(self, db_path: str = ":memory:"):
        self._engine = _create_engine(db_path)
        with self._engine.begin() as conn:
            self._migrate_legacy_schema(conn)
            schema.metadata.create_all(conn)

    def close(self) -> None:
        self._engine.dispose()

    def _migrate_legacy_schema(self, conn: Connection) -> None:
        """
        兼容旧库（Step 8 之前）: 当时的 orders 表承载的是逻辑仓位（无 role 列）。

        迁移动作: orders 改名 grid_positions; 旧意图快照 `entry_order` 字段改名为
        `entry_position`（仅字符串替换 id 表内的 JSON）。对全新库与非旧结构库幂等无感。
        """
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        if "orders" not in tables:
            return

        columns = {column["name"] for column in inspector.get_columns("orders")}
        if "role" in columns:
            return  # 已是新版订单流水表

        if "grid_positions" in tables:
            raise RuntimeError(
                "检测到旧版 orders（逻辑仓位）与新 grid_positions 并存, 数据状态不明确, 请人工处理"
            )

        logger.info("检测到旧版 orders 表（逻辑仓位形态）, 迁移为 grid_positions")
        conn.execute(text("ALTER TABLE orders RENAME TO grid_positions"))
        if "order_ids" in tables:
            conn.execute(
                text(
                    "UPDATE order_ids SET intent = REPLACE("
                    "intent, '\"entry_order\"', '\"entry_position\"')"
                )
            )

    # ---- id 表（WAL） ----

    def reserve(
        self,
        strategy_key: str,
        intent: OrderIntent,
        preferred_id: str | None = None,
    ) -> str:
        """
        预留一个订单号: 写入 id 表（used=0 + 意图快照）, 返回 custom_id

        @param strategy_key: 归属策略
        @param intent: 意图快照（对账重建仓位与流水所需上下文）
        @param preferred_id: 期望使用的订单号; 被占用时自动换新
        """
        for attempt in range(_MAX_ID_RETRY):
            custom_id = (
                preferred_id
                if attempt == 0 and preferred_id
                else self._generate_id(intent.side)
            )
            now = _now()
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        insert(schema.order_ids).values(
                            id=custom_id,
                            used=0,
                            type="ORDER",
                            strategy_key=strategy_key,
                            intent=intent.model_dump_json(),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                return custom_id
            except IntegrityError:
                continue
        raise RuntimeError(f"连续 {_MAX_ID_RETRY} 次未能生成唯一订单号")

    def pending_ids(self, strategy_key: str) -> list[IdRecord]:
        """未确认（待对账）的 id 列表"""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.order_ids)
                    .where(
                        schema.order_ids.c.strategy_key == strategy_key,
                        schema.order_ids.c.used == 0,
                    )
                    .order_by(schema.order_ids.c.created_at)
                )
                .mappings()
                .all()
            )
        return [self._to_id_record(row) for row in rows]

    def void(self, custom_id: str) -> None:
        """明确失败: 核销订单号, 不留仓位与流水"""
        with self._engine.begin() as conn:
            self._settle(conn, custom_id)

    # ---- 写（挂上成功: 仓位 + 流水 + 核销, 同一事务） ----

    def confirm_entry(
        self,
        custom_id: str,
        position: GridPositionRow,
        order: OrderRecord,
    ) -> None:
        """入场挂上成功: 同一事务写入/更新仓位行、记录订单流水并核销订单号"""
        with self._engine.begin() as conn:
            self._settle(conn, custom_id)
            self._upsert(conn, schema.grid_positions, "entry_id", position)
            self._upsert(conn, schema.orders, "order_id", order)

    def confirm_exit(
        self,
        custom_id: str,
        entry_ids: list[str],
        exit_id: str,
        exit_price: float,
        order: OrderRecord,
    ) -> None:
        """出场单挂上: 同一事务回填各仓位出场信息、记录订单流水并核销订单号"""
        with self._engine.begin() as conn:
            self._settle(conn, custom_id)
            record = self._get_id_record(conn, custom_id)
            if entry_ids:
                conn.execute(
                    update(schema.grid_positions)
                    .where(
                        schema.grid_positions.c.entry_id.in_(entry_ids),
                        schema.grid_positions.c.strategy_key == record.strategy_key,
                    )
                    .values(exit_id=exit_id, exit_price=exit_price, updated_at=_now())
                )
            self._upsert(conn, schema.orders, "order_id", order)

    # ---- 逻辑仓位表 ----

    def load_positions(self, strategy_key: str) -> list[GridPositionRow]:
        """加载策略当前仓位（active=1）"""
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
        return [self._to_position_row(row) for row in rows]

    def load_archived_positions(self, strategy_key: str) -> list[GridPositionRow]:
        """加载已归档仓位（active=0, 最近归档在前）—— 用于对账/盈亏统计"""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.grid_positions)
                    .where(
                        schema.grid_positions.c.strategy_key == strategy_key,
                        schema.grid_positions.c.active == 0,
                    )
                    .order_by(
                        schema.grid_positions.c.updated_at.desc(), text("rowid DESC")
                    )
                )
                .mappings()
                .all()
            )
        return [self._to_position_row(row) for row in rows]

    def deactivate_position(self, entry_id: str) -> None:
        """归档仓位（已平仓）: 保留行, 置 active=0"""
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.grid_positions)
                .where(schema.grid_positions.c.entry_id == entry_id)
                .values(active=0, updated_at=_now())
            )

    def sync_positions(
        self,
        strategy_key: str,
        rows: list[GridPositionRow],
        removed_ids: list[str],
    ) -> None:
        """
        事务同步当前仓位快照: upsert rows + 归档 removed_ids

        @param strategy_key: 归属策略（removed 归档也限定该策略, 防越界）
        @param rows: 当前仓位（快照, 覆盖旧值）
        @param removed_ids: 本次移除的仓位号（置 active=0）
        """
        with self._engine.begin() as conn:
            for row in rows:
                self._upsert(conn, schema.grid_positions, "entry_id", row)
            if removed_ids:
                conn.execute(
                    update(schema.grid_positions)
                    .where(
                        schema.grid_positions.c.entry_id.in_(removed_ids),
                        schema.grid_positions.c.strategy_key == strategy_key,
                    )
                    .values(active=0, updated_at=_now())
                )

    # ---- 订单流水表 ----

    def load_order_records(self, strategy_key: str) -> list[OrderRecord]:
        """加载策略的订单流水（写入序）"""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.orders)
                    .where(schema.orders.c.strategy_key == strategy_key)
                    .order_by(schema.orders.c.created_at, text("rowid"))
                )
                .mappings()
                .all()
            )
        return [self._to_order_record(row) for row in rows]

    # ---- 内部工具 ----

    def _generate_id(self, side: OrderSide) -> str:
        return f"{side.value}{secrets.token_hex(nbytes=5)}"

    def _settle(self, conn: Connection, custom_id: str) -> None:
        """核销订单号（used=1）; 未登记的订单号视为调用方错误"""
        result = conn.execute(
            update(schema.order_ids)
            .where(schema.order_ids.c.id == custom_id)
            .values(used=1, updated_at=_now())
        )
        if result.rowcount == 0:
            raise ValueError(f"订单号未在 id 表登记, 不能核销: {custom_id}")

    def _get_id_record(self, conn: Connection, custom_id: str) -> IdRecord:
        row = (
            conn.execute(
                select(schema.order_ids).where(schema.order_ids.c.id == custom_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError(f"订单号未在 id 表登记: {custom_id}")
        return self._to_id_record(row)

    def _upsert(
        self,
        conn: Connection,
        table: Table,
        primary_key: str,
        record: BaseModel,
    ) -> None:
        """通用 upsert: 列值取自记录（JSON 模式）; 冲突时更新除主键与 created_at 外的所有列"""
        now = _now()
        data: dict[str, Any] = record.model_dump(mode="json")
        data["created_at"] = data["created_at"] or now
        data["updated_at"] = now
        stmt = sqlite_insert(table).values(**data)
        set_: dict[str, Any] = {
            name: stmt.excluded[name]
            for name in data
            if name not in (primary_key, "created_at")
        }
        conn.execute(stmt.on_conflict_do_update(index_elements=[primary_key], set_=set_))

    def _to_id_record(self, row: RowMapping) -> IdRecord:
        data = dict(row)
        return IdRecord.model_validate(
            {**data, "intent": OrderIntent.model_validate_json(data["intent"])}
        )

    def _to_position_row(self, row: RowMapping) -> GridPositionRow:
        return GridPositionRow.model_validate(dict(row))

    def _to_order_record(self, row: RowMapping) -> OrderRecord:
        return OrderRecord.model_validate(dict(row))
