"""002: 新增 commands 控制面表（Step 31）

用法:
    uv run python scripts/migrations/002_add_commands.py <db_path>

幂等: 表 / 索引已存在时跳过, 重复执行无害。
约定见 001 脚本头: schema.py 是"当前结构"的唯一声明, 业务包不维护迁移历史。
"""

import sqlite3
import sys

_DDL = """
CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    strategy_key TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    requested_by TEXT NOT NULL DEFAULT 'web',
    idempotency_key TEXT UNIQUE,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_commands_strategy ON commands(strategy_key, requested_at);
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/002_add_commands.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='commands'"
        ).fetchone()
        conn.executescript(_DDL)
        conn.commit()
        print(
            "skipped: commands table already exists"
            if existing
            else "migrated: commands table"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
