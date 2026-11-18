"""007: 新增 strategy_kline_state 每策略完成K线水位表

用法:
    uv run python scripts/migrations/007_add_strategy_kline_state.py <db_path>

用途: 进程停机期间可能错过反向离场信号; 重启时以最后处理的完成K线
为起点回放 historcal K 线并补挂限价单。

幂等: 表已存在时跳过, 重复执行无害。
约定见 001 脚本头: schema.py 是"当前结构"的唯一声明, 业务包不维护迁移历史。
"""

import sqlite3
import sys

_DDL = """
CREATE TABLE IF NOT EXISTS strategy_kline_state (
    strategy_key TEXT PRIMARY KEY,
    last_finished_kline_ms INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "007_add_strategy_kline_state.py <db_path>"
        )
        return 2

    conn = sqlite3.connect(args[0])
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='strategy_kline_state'"
        ).fetchone()
        conn.executescript(_DDL)
        conn.commit()
        print(
            "skipped: strategy_kline_state table already exists"
            if existing
            else "migrated: strategy_kline_state table"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
