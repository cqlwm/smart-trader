"""统一入口测试: reload 开关识别与工厂装配（不触网）"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.importer import import_from_string

import run


def test_reload_flag_is_recognized() -> None:
    """只有显式 --reload 才进入开发热重载模式"""
    assert run.reload_requested(["--reload"]) is True
    assert run.reload_requested([]) is False


def test_uvicorn_resolves_factory_entrypoint() -> None:
    """--reload 使用导入字符串加载工厂, 命名漂移会直接启动失败"""
    factory = import_from_string("run:create_application")

    assert factory is run.create_application


def test_create_application_builds_console_against_given_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """工厂入口必须完成账本/运行时装配, 并走完整 lifespan 启动与关闭"""
    monkeypatch.setattr(
        "smart_trader.config.DB_PATH", str(tmp_path / "smart_trader.db")
    )
    monkeypatch.setenv("SMART_TRADER_API_TOKEN", "test-token")

    app = run.create_application()

    with TestClient(app) as client:
        response = client.get(
            "/api/health", headers={"Authorization": "Bearer test-token"}
        )

    assert response.status_code == 200, "工厂装配的控制台应可正常响应"
    assert response.json()["database"].endswith("smart_trader.db")
