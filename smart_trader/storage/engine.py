"""sqlite 引擎与结构校验（写仓储与只读仓储共用）

连接参数、PRAGMA 与"当前结构"校验只在这里出现一次:
`OrderJournal`（写）在此基础上建表, `OrderJournalReader`（只读）只校验不建表。
"""

from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

from smart_trader.storage import schema


def _on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    """每个物理连接建立时设置: WAL（内存库自动忽略）+ 写锁等待 30s"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_engine_for_path(db_path: str) -> Engine:
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


def require_current_schema(conn: Connection) -> None:
    """打开即校验库结构与 schema 一致; 缺列时报错内联可执行的 ALTER 语句"""
    existing = {
        str(row[1]) for row in conn.execute(text("PRAGMA table_info(grid_positions)"))
    }
    missing = [
        column for column in schema.grid_positions.columns if column.name not in existing
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
