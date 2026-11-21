"""只读 API 路由测试: 参数校验 / 404 / 只读契约 / token / 静态托管"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.model import OrderStatus, PositionCloseReason
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from tests.api_harness import (
    AUTH_HEADERS,
    TEST_TOKEN,
    make_order,
    make_position,
    seed_position,
)


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
def client(reader: OrderJournalReader, commands: CommandStore) -> Iterator[TestClient]:
    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN), headers=AUTH_HEADERS
    ) as instance:
        yield instance


def test_lifespan_runs_startup_and_shutdown_hooks(
    reader: OrderJournalReader, commands: CommandStore
) -> None:
    """统一入口依赖这两个钩子管理策略运行时"""
    events: list[str] = []

    app = create_app(
        reader,
        commands,
        token=TEST_TOKEN,
        on_startup=lambda: events.append("start"),
        on_shutdown=lambda: events.append("stop"),
    )
    with TestClient(app):
        assert events == ["start"], "进入应用时必须执行启动钩子"
    assert events == ["start", "stop"], "退出应用时必须执行停止钩子"


def _seed_single_position(journal: OrderJournal) -> None:
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            ),
            make_order(
                "buy2",
                "POS1",
                status=None,
                quantity=2.0,
                price=0.4,
            ),
        ],
    )


def test_health_returns_database_and_status(
    journal: OrderJournal, client: TestClient
):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["status"] == "ok"
    assert body["database"].endswith("smart_trader.db")


def test_overview_endpoint_returns_summary(
    journal: OrderJournal, client: TestClient
):
    _seed_single_position(journal)

    response = client.get("/api/overview")

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["strategy_count"] == 1
    assert body["active_position_count"] == 1
    assert body["unconfirmed_order_count"] == 1
    assert len(body["strategies"]) == 1


def test_positions_endpoint_accepts_filters(
    journal: OrderJournal, client: TestClient
):
    _seed_single_position(journal)

    active = client.get("/api/positions", params={"strategy_key": "dogeusdt_long_buy_1m"})
    archived = client.get("/api/positions", params={"include_closed": "true"})

    assert active.status_code == 200
    assert len(cast(list[dict[str, Any]], active.json())) == 1
    assert archived.status_code == 200


def test_position_detail_returns_404_for_unknown_id(client: TestClient):
    response = client.get("/api/positions/missing")

    assert response.status_code == 404


def test_archived_positions_endpoint_returns_paginated_page(
    journal: OrderJournal, client: TestClient
):
    _seed_single_position(journal)
    seed_position(
        journal,
        make_position("POS2"),
        [make_order("buy-pos2", "POS2")],
    )
    journal.close_position("POS2", PositionCloseReason.EXIT_SETTLED)

    response = client.get(
        "/api/positions/archived", params={"limit": 20, "offset": 0}
    )

    assert response.status_code == 200, "archived 路由不得被 /positions/{id} 吞掉"
    body = cast(dict[str, Any], response.json())
    assert body["total"] == 1
    assert body["limit"] == 20
    assert body["offset"] == 0
    items = cast(list[dict[str, Any]], body["items"])
    assert [item["id"] for item in items] == ["POS2"]
    assert items[0]["active"] is False


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}]
)
def test_archived_positions_endpoint_rejects_out_of_range_paging(
    client: TestClient, params: dict[str, int]
):
    response = client.get("/api/positions/archived", params=params)

    assert response.status_code == 422


def test_orders_endpoint_rejects_unknown_status(client: TestClient):
    response = client.get("/api/orders", params={"status": "bogus"})

    assert response.status_code == 422, "非法状态由 FastAPI 参数校验拦截"


@pytest.mark.parametrize("limit", [0, 501])
def test_orders_endpoint_rejects_out_of_range_limit(
    client: TestClient, limit: int
):
    response = client.get("/api/orders", params={"limit": limit})

    assert response.status_code == 422


def test_openapi_write_surface_is_commands_and_configs_only(client: TestClient):
    payload = cast(dict[str, Any], client.get("/openapi.json").json())
    paths = cast(dict[str, dict[str, Any]], payload["paths"])
    methods = {
        method.lower() for operations in paths.values() for method in operations
    }
    post_paths = {
        path for path, operations in paths.items() if "post" in operations
    }
    put_paths = {path for path, operations in paths.items() if "put" in operations}

    assert methods == {"get", "post", "put"}, f"写面只允许命令与配置, 实际: {methods}"
    assert post_paths == {
        "/api/strategies/{strategy_key}/commands",
        "/api/strategies/{strategy_key}/disable",
        "/api/strategies/{strategy_key}/enable",
        "/api/commands/kill-switch",
        "/api/configs",
        "/api/strategies/{strategy_key}/position-acks",
        "/api/position-acks/{ack_id}/revoke",
    }, f"POST 只允许命令提交、启停、新建配置与仓位声明, 实际: {post_paths}"
    assert put_paths == {"/api/configs/{strategy_key}"}, (
        f"PUT 只允许更新配置, 实际: {put_paths}"
    )


def test_write_methods_are_rejected(client: TestClient):
    assert client.post("/api/orders").status_code == 405
    assert client.delete("/api/positions/POS1").status_code == 405


def test_requires_bearer_token(
    reader: OrderJournalReader, commands: CommandStore
):
    with TestClient(create_app(reader, commands, token="secret")) as guarded:
        assert guarded.get("/api/overview").status_code == 401

        wrong = guarded.get(
            "/api/overview", headers={"Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401

        ok = guarded.get(
            "/api/overview", headers={"Authorization": "Bearer secret"}
        )
        assert ok.status_code == 200


def test_static_directory_serves_index(
    reader: OrderJournalReader, commands: CommandStore, tmp_path: Path
):
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>console</html>", encoding="utf-8")

    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN, static_dir=static_dir)
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "console" in response.text
