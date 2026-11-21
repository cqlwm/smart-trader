"""008: 策略配置 payload 回填 entry_mode（加仓模式）

用法:
    uv run python scripts/migrations/008_add_entry_mode.py <db_path>

幂等: 已含 `entry_mode` 的 payload 跳过, 重复执行无害。

背景: 加仓间距口径由"`trend_spacing_rate` 是否为空"的隐式判断改为显式
`entry_mode`（`grid` / `trend`）。存量配置按原行为回填：
- 有 `trend_spacing_rate` → `trend`，并清空 `grid_spacing_rate`
  （`trend` 模式下无关字段必须为空）；
- 否则 → `grid`，保留原网格间距。
"""

import json
import sqlite3
import sys
from typing import Any, cast

_SELECT_SQL = "SELECT strategy_key, payload FROM strategy_configs"
_UPDATE_SQL = "UPDATE strategy_configs SET payload = ? WHERE strategy_key = ?"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "008_add_entry_mode.py <db_path>"
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

        migrated = 0
        skipped = 0
        for strategy_key, payload in conn.execute(_SELECT_SQL).fetchall():
            data: Any = json.loads(payload)
            if not isinstance(data, dict):
                print(f"invalid payload: {strategy_key}")
                return 1
            payload_data = cast(dict[str, Any], data)
            if "entry_mode" in payload_data:
                skipped += 1
                continue
            if payload_data.get("trend_spacing_rate") is not None:
                payload_data["entry_mode"] = "trend"
                payload_data["grid_spacing_rate"] = None
            elif payload_data.get("grid_spacing_rate") is None:
                print(f"invalid payload: {strategy_key} (无可用间距字段)")
                return 1
            else:
                payload_data["entry_mode"] = "grid"
            conn.execute(
                _UPDATE_SQL,
                (
                    json.dumps(payload_data, ensure_ascii=False, sort_keys=True),
                    strategy_key,
                ),
            )
            migrated += 1
        conn.commit()
        print(f"done: migrated={migrated} skipped={skipped}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
