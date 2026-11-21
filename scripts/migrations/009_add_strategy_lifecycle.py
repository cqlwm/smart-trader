"""009: strategy_configs 增加生命周期字段（停用编排 / 暂停持久化）

用法:
    uv run python scripts/migrations/009_add_strategy_lifecycle.py <db_path>

幂等: 按 PRAGMA 检查列是否存在, 只补缺失列; 重复执行无害。

背景: 停用从"仅改 enabled 标记"升级为异步编排流程, 暂停状态需要跨重启保留。
存量配置按原状态回填:
- enabled=1 → lifecycle=active, desired_running=1;
- enabled=0 → lifecycle=disabled, desired_running=0。
"""

import sqlite3
import sys

_COLUMNS: list[tuple[str, str]] = [
    ("desired_running", "INTEGER NOT NULL DEFAULT 1"),
    ("lifecycle", "TEXT NOT NULL DEFAULT 'active'"),
    ("disable_policy", "TEXT"),
    ("disable_requested_at", "TEXT"),
    ("disable_completed_at", "TEXT"),
    ("disable_error", "TEXT"),
]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "009_add_strategy_lifecycle.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='strategy_configs'"
        ).fetchone()
        if existing is None:
            print(
                "strategy_configs 表不存在; 请先执行 "
                "003_add_strategy_configs.py <db_path>"
            )
            return 1

        present = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(strategy_configs)")
        }
        added: set[str] = set()
        for name, ddl in _COLUMNS:
            if name in present:
                continue
            conn.execute(f"ALTER TABLE strategy_configs ADD COLUMN {name} {ddl}")
            added.add(name)

        lifecycle_added = "lifecycle" in added
        if lifecycle_added:
            conn.execute(
                "UPDATE strategy_configs "
                "SET lifecycle = CASE WHEN enabled = 1 THEN 'active' ELSE 'disabled' END, "
                "desired_running = CASE WHEN enabled = 1 THEN 1 ELSE 0 END"
            )
        elif "desired_running" in added:
            conn.execute(
                "UPDATE strategy_configs SET desired_running = enabled"
            )

        conn.commit()
        print(f"done: added={len(added)} columns={sorted(added)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
