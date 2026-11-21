"""008 迁移脚本: 按存量间距字段回填 entry_mode"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from smart_trader.storage.config_store import ConfigStore
from smart_trader.storage.order_journal import OrderJournal

SCRIPT = (
    Path(__file__).parent.parent
    / "scripts"
    / "migrations"
    / "008_add_entry_mode.py"
)


def _run(db_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _seed(db_path: Path) -> None:
    """写入迁移前的两类 payload: 网格口径与顺势口径"""
    journal = OrderJournal(str(db_path))
    store = ConfigStore(str(db_path))
    try:
        store.create(
            strategy_key="grid",
            payload=json.dumps(
                {"symbol": "DOGE/USDT", "grid_spacing_rate": 0.01}
            ),
        )
        store.create(
            strategy_key="trend",
            payload=json.dumps(
                {
                    "symbol": "DOGE/USDT",
                    "grid_spacing_rate": 0.01,
                    "trend_spacing_rate": 0.0002,
                }
            ),
        )
    finally:
        store.close()
        journal.close()


def _payloads(db_path: Path) -> dict[str, dict[str, Any]]:
    journal = OrderJournal(str(db_path))
    store = ConfigStore(str(db_path))
    try:
        return {
            record.strategy_key: cast(dict[str, Any], json.loads(record.payload))
            for record in store.list()
        }
    finally:
        store.close()
        journal.close()


def test_migration_backfills_entry_mode_and_clears_grid_for_trend(
    tmp_path: Path,
):
    db_path = tmp_path / "smart_trader.db"
    _seed(db_path)

    result = _run(db_path)

    assert result.returncode == 0, f"迁移失败: {result.stdout} {result.stderr}"
    payloads = _payloads(db_path)
    assert payloads["grid"]["entry_mode"] == "grid"
    assert payloads["grid"]["grid_spacing_rate"] == pytest.approx(0.01)
    assert payloads["trend"]["entry_mode"] == "trend", (
        "有 trend_spacing_rate 的存量配置必须回填为 trend 模式"
    )
    assert payloads["trend"]["grid_spacing_rate"] is None, (
        "顺势模式必须清空无关的网格间距"
    )
    assert payloads["trend"]["trend_spacing_rate"] == pytest.approx(0.0002)


def test_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "smart_trader.db"
    _seed(db_path)
    assert _run(db_path).returncode == 0
    first = _payloads(db_path)

    result = _run(db_path)

    assert result.returncode == 0
    assert "migrated=0 skipped=2" in result.stdout, "重复执行必须跳过全部行"
    assert _payloads(db_path) == first
