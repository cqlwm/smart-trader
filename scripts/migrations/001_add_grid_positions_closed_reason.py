"""001: grid_positions 增加 closed_reason 审计列（Step 28）

用法:
    uv run python scripts/migrations/001_add_grid_positions_closed_reason.py <db_path>

幂等: 列已存在时跳过, 重复执行无害。
约定: 每次库结构演化在 scripts/migrations/ 下新增下一个编号的脚本,
手动对每个存量库执行一次; OrderJournal 打开缺列旧库会报错并给出等价
ALTER 语句（schema.py 是"当前结构"的唯一声明, 业务包不维护迁移历史）。
"""

import sqlite3
import sys

_ALTER = "ALTER TABLE grid_positions ADD COLUMN closed_reason TEXT"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "001_add_grid_positions_closed_reason.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(grid_positions)")
        }
        if "closed_reason" in existing:
            print("skipped: closed_reason already exists")
            return 0
        conn.execute(_ALTER)
        conn.commit()
        print(f"migrated: {_ALTER}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
