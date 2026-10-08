"""存储层 schema 与冻结契约的等价性测试

`schema.py` 的 SQLAlchemy 元数据建出的库结构, 必须与迁移前的硬编码 DDL
（本文件底部的冻结快照）完全一致: 列名 / 类型 / NOT NULL / 默认值 / 主键 / 索引。

这份快照是"库结构契约 v1":
- 有意变更库结构时, 必须同步更新本快照并评估旧库迁移;
- 无意的结构漂移（改漏一列、类型写错、索引丢失）会在本测试中被抓住。

归一化说明: SQLite 对 `TEXT PRIMARY KEY` 不隐含 NOT NULL（历史遗留行为）,
SQLAlchemy 建表会显式 NOT NULL; 两者语义等价, 比较时按 `notnull or pk` 归一。
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from smart_trader.storage import schema
from smart_trader.storage.order_journal import OrderJournal

_TABLES = ("order_ids", "grid_positions", "orders")


def _normalize_columns(
    rows: list[tuple[Any, ...]],
) -> list[tuple[Any, ...]]:
    """PRAGMA table_info 归一化: (列名, 类型, notnull或pk, 默认值, pk)"""
    return [(row[1], row[2], row[3] or row[5], row[4], row[5]) for row in rows]


def _snapshot(
    execute: Callable[[str], list[tuple[Any, ...]]],
) -> dict[str, dict[str, Any]]:
    """库结构快照: 每张表的列定义与索引（索引名 + 索引列）"""
    snapshot: dict[str, dict[str, Any]] = {}
    for table in _TABLES:
        columns = _normalize_columns(execute(f"PRAGMA table_info({table})"))
        index_names = sorted(str(row[1]) for row in execute(f"PRAGMA index_list({table})"))
        indexes = {
            name: [tuple(row) for row in execute(f"PRAGMA index_info({name})")]
            for name in index_names
        }
        snapshot[table] = {"columns": columns, "indexes": indexes}
    return snapshot


def _legacy_snapshot() -> dict[str, dict[str, Any]]:
    """契约基准: 旧版硬编码 DDL 建出的结构"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_FROZEN_DDL)
        return _snapshot(
            lambda sql: [tuple(row) for row in conn.execute(sql).fetchall()]
        )
    finally:
        conn.close()


def _metadata_snapshot() -> dict[str, dict[str, Any]]:
    """schema.metadata 建出的结构"""
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            schema.metadata.create_all(conn)
            return _snapshot(
                lambda sql: [tuple(row) for row in conn.execute(text(sql))]
            )
    finally:
        engine.dispose()


def _journal_file_snapshot(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """OrderJournal 通过真实文件路径建出的结构"""
    path = str(tmp_path / "schema.db")
    journal = OrderJournal(path)
    journal.close()
    conn = sqlite3.connect(path)
    try:
        return _snapshot(
            lambda sql: [tuple(row) for row in conn.execute(sql).fetchall()]
        )
    finally:
        conn.close()


class TestSchemaEquivalence:
    def test_metadata_matches_frozen_contract(self):
        assert _metadata_snapshot() == _legacy_snapshot(), (
            "schema.metadata 建出的库结构必须与冻结契约完全一致"
        )

    def test_journal_created_database_matches_frozen_contract(self, tmp_path: Path):
        assert _journal_file_snapshot(tmp_path) == _legacy_snapshot(), (
            "OrderJournal 实际建出的库结构必须与冻结契约完全一致"
        )

    def test_metadata_defines_exactly_expected_tables(self):
        assert set(schema.metadata.tables) == set(_TABLES), (
            "metadata 只应包含三张订单日志表, 不得多也不得少"
        )


# ---- 冻结契约（迁移前的硬编码 DDL 快照, 勿随手修改, 见文件头说明） ----

_FROZEN_DDL = """
CREATE TABLE IF NOT EXISTS order_ids (
    id TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL DEFAULT 'ORDER',
    strategy_key TEXT NOT NULL,
    intent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_ids_pending ON order_ids(strategy_key, used);

CREATE TABLE IF NOT EXISTS grid_positions (
    entry_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    filled_quantity REAL NOT NULL DEFAULT 0,
    fixed_take_profit_rate REAL NOT NULL,
    signal_min_take_profit_rate REAL NOT NULL,
    exit_price REAL,
    status TEXT,
    exit_id TEXT,
    stop_loss_rate REAL NOT NULL DEFAULT 0,
    enable_stop_loss INTEGER NOT NULL DEFAULT 0,
    trailing_stop_rate REAL NOT NULL DEFAULT 0,
    enable_trailing_stop INTEGER NOT NULL DEFAULT 0,
    trailing_stop_activation_profit_rate REAL NOT NULL DEFAULT 0,
    current_stop_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grid_positions_strategy ON grid_positions(strategy_key, active);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    role TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    price REAL,
    quantity REAL NOT NULL DEFAULT 0,
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_price REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_key, role);
"""
