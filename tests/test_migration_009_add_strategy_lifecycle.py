"""009 迁移脚本: 补充生命周期字段并回填存量启停状态"""

import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).parent.parent
    / "scripts"
    / "migrations"
    / "009_add_strategy_lifecycle.py"
)

_LEGACY_DDL = """
CREATE TABLE strategy_configs (
    strategy_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _run(db_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _seed_legacy(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_LEGACY_DDL)
        for key, enabled in (("enabled", 1), ("disabled", 0)):
            conn.execute(
                "INSERT INTO strategy_configs "
                "(strategy_key, payload, version, enabled, updated_by, created_at, updated_at) "
                "VALUES (?, '{}', 1, ?, 'web', 't', 't')",
                (key, enabled),
            )
        conn.commit()
    finally:
        conn.close()


def _rows(db_path: Path) -> dict[str, tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            str(key): (int(desired), str(lifecycle))
            for key, desired, lifecycle in conn.execute(
                "SELECT strategy_key, desired_running, lifecycle "
                "FROM strategy_configs"
            )
        }
    finally:
        conn.close()


def test_migration_adds_columns_and_backfills_lifecycle(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    _seed_legacy(db_path)

    result = _run(db_path)

    assert result.returncode == 0, f"迁移失败: {result.stdout} {result.stderr}"
    assert _rows(db_path) == {
        "enabled": (1, "active"),
        "disabled": (0, "disabled"),
    }, "存量配置必须按 enabled 回填暂停意图与生命周期"

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(strategy_configs)")
        }
    finally:
        conn.close()
    assert {
        "desired_running",
        "lifecycle",
        "disable_policy",
        "disable_requested_at",
        "disable_completed_at",
        "disable_error",
    } <= columns


def test_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    _seed_legacy(db_path)
    assert _run(db_path).returncode == 0
    first = _rows(db_path)

    result = _run(db_path)

    assert result.returncode == 0
    assert "added=0" in result.stdout, "重复执行不得重复加列"
    assert _rows(db_path) == first
