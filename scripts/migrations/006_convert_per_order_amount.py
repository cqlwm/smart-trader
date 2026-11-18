"""006: 策略配置 payload 的 per_order_qty 转 per_order_amount + per_order_unit

用法:
    uv run python scripts/migrations/006_convert_per_order_amount.py <db_path>

幂等: 只处理仍含 `per_order_qty` 的 payload; 已转换的行跳过, 重复执行无害。

背景: 单层规模由"纯币数量"升级为"金额 + 单位"（base / quote）。
存量配置统一按 `base` 迁移, 保持原有下单数量语义不变。
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
            "006_convert_per_order_amount.py <db_path>"
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
            if "per_order_qty" not in payload_data:
                skipped += 1
                continue
            payload_data["per_order_amount"] = payload_data.pop("per_order_qty")
            payload_data["per_order_unit"] = "base"
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
