"""API 鉴权与 CORS: 强制 Bearer token / 启动校验 / 跨域预检"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from tests.api_harness import AUTH_HEADERS, TEST_TOKEN

ALLOWED_ORIGIN = "http://localhost:5173"
UNKNOWN_ORIGIN = "http://evil.example"

# 覆盖读面、写面与全局命令: 所有 /api 路由都必须先过鉴权
PROTECTED_REQUESTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/health", None),
    ("GET", "/api/overview", None),
    ("GET", "/api/strategies", None),
    ("GET", "/api/positions", None),
    ("GET", "/api/positions/POS1", None),
    ("GET", "/api/orders", None),
    ("GET", "/api/commands", None),
    ("GET", "/api/commands/CMD1", None),
    ("GET", "/api/configs", None),
    ("GET", "/api/configs/defaults", None),
    ("GET", "/api/configs/some_key", None),
    ("GET", "/api/position-acks", None),
    ("GET", "/api/market/chart", None),
    ("POST", "/api/configs", {}),
    ("PUT", "/api/configs/some_key", {}),
    ("POST", "/api/strategies/some_key/commands", {"action": "pause"}),
    ("POST", "/api/commands/kill-switch", {}),
    ("POST", "/api/strategies/some_key/position-acks", {"external_qty": 1}),
    ("POST", "/api/position-acks/ACK1/revoke", {}),
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def reader(db_path: Path, journal: OrderJournal) -> Iterator[OrderJournalReader]:
    instance = OrderJournalReader(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def commands(db_path: Path, journal: OrderJournal) -> Iterator[CommandStore]:
    instance = CommandStore(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def client(
    reader: OrderJournalReader, commands: CommandStore
) -> Iterator[TestClient]:
    with TestClient(
        create_app(
            reader,
            commands,
            token=TEST_TOKEN,
            cors_origins=[ALLOWED_ORIGIN],
        ),
        headers=AUTH_HEADERS,
    ) as instance:
        yield instance


@pytest.fixture
def unauthenticated_client(
    reader: OrderJournalReader, commands: CommandStore
) -> Iterator[TestClient]:
    """不带默认 Authorization 头, 用于验证 401"""
    with TestClient(
        create_app(
            reader,
            commands,
            token=TEST_TOKEN,
            cors_origins=[ALLOWED_ORIGIN],
        )
    ) as instance:
        yield instance


@pytest.mark.parametrize("token", [None, "", "   "])
def test_create_app_rejects_blank_token(
    reader: OrderJournalReader,
    commands: CommandStore,
    token: str | None,
) -> None:
    """为空即拒绝启动, 避免服务以无鉴权状态暴露"""
    with pytest.raises(ValueError, match="SMART_TRADER_API_TOKEN"):
        create_app(reader, commands, token=token)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    PROTECTED_REQUESTS,
    ids=[f"{method} {path}" for method, path, _ in PROTECTED_REQUESTS],
)
def test_every_api_route_requires_bearer_token(
    unauthenticated_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = unauthenticated_client.request(method, path, json=body)
    assert response.status_code == 401, (
        f"{method} {path} 必须拒绝无 Key 请求, 实际 {response.status_code}"
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    PROTECTED_REQUESTS,
    ids=[f"{method} {path}" for method, path, _ in PROTECTED_REQUESTS],
)
def test_every_api_route_rejects_wrong_token(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = client.request(
        method,
        path,
        json=body,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401, (
        f"{method} {path} 必须拒绝错误 Key, 实际 {response.status_code}"
    )


def test_correct_token_is_accepted(client: TestClient) -> None:
    response = client.get("/api/overview", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_cors_preflight_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/overview",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200, "预检请求不应被鉴权拦截"
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_rejects_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/api/overview",
        headers={
            "Origin": UNKNOWN_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_response_exposes_allow_origin(client: TestClient) -> None:
    response = client.get(
        "/api/overview",
        headers={**AUTH_HEADERS, "Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_cors_absent_when_not_configured(
    reader: OrderJournalReader, commands: CommandStore
) -> None:
    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN), headers=AUTH_HEADERS
    ) as instance:
        response = instance.get(
            "/api/overview", headers={"Origin": ALLOWED_ORIGIN}
        )

    assert "access-control-allow-origin" not in response.headers
