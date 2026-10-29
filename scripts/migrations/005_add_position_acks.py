"""005: 新增 position_acks 人工仓位声明表

用法:
    uv run python scripts/migrations/005_add_position_acks.py <db_path>

幂等: 表 / 索引已存在时跳过, 重复执行无害。
约定见 001 脚本头: schema.py 是"当前结构"的唯一声明, 业务包不维护迁移历史。
"""

import sqlite3
import sys

_DDL = """
CREATE TABLE IF NOT EXISTS position_acks (
    id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    position_side TEXT NOT NULL,
    external_qty REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL DEFAULT 'web',
    acknowledged_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_acks_active ON position_acks(strategy_key, status);
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "005_add_position_acks.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='position_acks'"
        ).fetchone()
        conn.executescript(_DDL)
        conn.commit()
        print(
            "skipped: position_acks table already exists"
            if existing
            else "migrated: position_acks table"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
