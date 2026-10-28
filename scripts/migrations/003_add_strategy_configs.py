"""003: 新增 strategy_configs 策略配置表（P3）

用法:
    uv run python scripts/migrations/003_add_strategy_configs.py <db_path>

幂等: 表已存在时跳过, 重复执行无害。
约定见 001 脚本头: schema.py 是"当前结构"的唯一声明, 业务包不维护迁移历史。
"""

import sqlite3
import sys

_DDL = """
CREATE TABLE IF NOT EXISTS strategy_configs (
    strategy_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "003_add_strategy_configs.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='strategy_configs'"
        ).fetchone()
        conn.executescript(_DDL)
        conn.commit()
        print(
            "skipped: strategy_configs table already exists"
            if existing
            else "migrated: strategy_configs table"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
