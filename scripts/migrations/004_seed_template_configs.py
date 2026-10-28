"""004: 从模板配置播种 strategy_configs（P3）

用法:
    uv run python scripts/migrations/004_seed_template_configs.py <db_path>

幂等: `INSERT OR IGNORE`, 已存在的策略键跳过, 不覆盖用户已编辑的配置。
前置: 先执行 003_add_strategy_configs.py。
"""

import sqlite3
import sys
from datetime import UTC, datetime

from smart_trader.model import DataSource
from smart_trader.strategy.config_payload import StrategyConfigPayload
from template import dogeusdt, zecusdt_trend

_INSERT_SQL = """
INSERT OR IGNORE INTO strategy_configs
    (strategy_key, payload, version, enabled, updated_by, created_at, updated_at)
VALUES (?, ?, 1, 1, 'seed', ?, ?)
"""


def _seeds() -> list[tuple[str, str]]:
    """(strategy_key, payload JSON) 列表; 模板是唯一来源"""
    items = [
        (dogeusdt.grid_config(), DataSource.TIMER),
        (zecusdt_trend.trend_short_config(), DataSource.WS),
    ]
    return [
        (
            config.strategy_key,
            StrategyConfigPayload.from_strategy_config(
                config, data_source=data_source
            ).to_json(),
        )
        for config, data_source in items
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: uv run python scripts/migrations/"
            "004_seed_template_configs.py <db_path>"
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

        now = datetime.now(UTC).isoformat()
        inserted = 0
        skipped = 0
        for strategy_key, payload in _seeds():
            cursor = conn.execute(_INSERT_SQL, (strategy_key, payload, now, now))
            if cursor.rowcount == 1:
                inserted += 1
                print(f"seeded: {strategy_key}")
            else:
                skipped += 1
                print(f"skipped: {strategy_key}")
        conn.commit()
        print(f"done: inserted={inserted} skipped={skipped}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
