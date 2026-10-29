"""人工仓位声明 API: 创建 / 替换 / 撤销 / 目标校验 / 告警抑制 / 鉴权"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.model import ReconcileSnapshot
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.position_ack_store import PositionAckStore
from smart_trader.storage.reader import OrderJournalReader
from tests.test_command_runner import KEY, FakeControllableStrategy


def _reconcile(
    *, exchange: float = 3500.0, local: float = 500.0, gap: float = -3000.0
) -> ReconcileSnapshot:
    return ReconcileSnapshot(
        checked_at=datetime.now(UTC).isoformat(),
        exchange_qty=exchange,
        local_qty=local,
        gap=gap,
        tracked_exit_orders=0,
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
def ack_store(db_path: Path, journal: OrderJournal) -> Iterator[PositionAckStore]:
    instance = PositionAckStore(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def runtime_path(tmp_path: Path) -> Path:
    path = tmp_path / "runtime.json"
    RuntimeRegistry(path).heartbeat(
        [FakeControllableStrategy(KEY, reconcile=_reconcile())]
    )
    return path


@pytest.fixture
def client(
    reader: OrderJournalReader,
    commands: CommandStore,
    ack_store: PositionAckStore,
    runtime_path: Path,
) -> Iterator[TestClient]:
    with TestClient(
        create_app(
            reader,
            commands,
            runtime_path=runtime_path,
            ack_store=ack_store,
        )
    ) as instance:
        yield instance


def _create(client: TestClient, external_qty: float = 3000.0, note: str = "手工仓"):
    return client.post(
        f"/api/strategies/{KEY}/position-acks",
        json={"external_qty": external_qty, "note": note},
    )


def test_create_persists_active_ack(client: TestClient, ack_store: PositionAckStore):
    response = _create(client)

    assert response.status_code == 201
    body = cast(dict[str, Any], response.json())
    assert body["status"] == "active"
    assert body["external_qty"] == 3000.0
    assert body["strategy_key"] == KEY

    active = ack_store.active_for(KEY)
    assert active is not None
    assert active.external_qty == 3000.0
    assert active.note == "手工仓"


def test_create_requires_live_strategy(
    reader: OrderJournalReader,
    commands: CommandStore,
    ack_store: PositionAckStore,
    tmp_path: Path,
):
    missing_runtime = tmp_path / "missing-runtime.json"
    with TestClient(
        create_app(
            reader,
            commands,
            runtime_path=missing_runtime,
            ack_store=ack_store,
        )
    ) as bare:
        response = _create(bare)

    assert response.status_code == 409
    assert "心跳" in cast(dict[str, Any], response.json())["detail"]


def test_create_rejects_when_no_surplus(
    client: TestClient, runtime_path: Path
):
    RuntimeRegistry(runtime_path).heartbeat(
        [
            FakeControllableStrategy(
                KEY,
                reconcile=_reconcile(exchange=500.0, local=500.0, gap=0.0),
            )
        ]
    )

    response = _create(client)

    assert response.status_code == 409
    assert "没有" in cast(dict[str, Any], response.json())["detail"]


def test_create_rejects_quantity_above_surplus(client: TestClient):
    response = _create(client, external_qty=4000.0)

    assert response.status_code == 422
    assert "大于当前差额" in cast(dict[str, Any], response.json())["detail"]


def test_create_rejects_non_positive_quantity(client: TestClient):
    response = _create(client, external_qty=0.0)

    assert response.status_code == 422


def test_create_replaces_previous_ack(
    client: TestClient, ack_store: PositionAckStore
):
    assert _create(client, external_qty=3000.0).status_code == 201
    second = _create(client, external_qty=2500.0, note="缩减")

    assert second.status_code == 201
    active = ack_store.active_for(KEY)
    assert active is not None and active.external_qty == 2500.0
    assert ack_store.count() == 2, "旧声明必须保留审计"

    page = client.get("/api/position-acks")
    assert page.status_code == 200
    body = cast(dict[str, Any], page.json())
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_revoke_is_idempotent_and_keeps_history(
    client: TestClient, ack_store: PositionAckStore
):
    created = cast(dict[str, Any], _create(client).json())
    ack_id = str(created["id"])

    first = client.post(f"/api/position-acks/{ack_id}/revoke")
    second = client.post(f"/api/position-acks/{ack_id}/revoke")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["status"] == "revoked"
    assert second.json()["status"] == "revoked"
    assert ack_store.active_for(KEY) is None
    assert ack_store.count() == 1, "撤销不得删除历史"


def test_revoke_unknown_ack_returns_404(client: TestClient):
    assert client.post("/api/position-acks/ACKmissing/revoke").status_code == 404


def test_delete_ack_is_not_allowed(client: TestClient, ack_store: PositionAckStore):
    created = cast(dict[str, Any], _create(client).json())
    ack_id = str(created["id"])

    response = client.delete(f"/api/position-acks/{ack_id}")

    assert response.status_code in (404, 405), "不得提供物理删除接口"
    assert ack_store.count() == 1, "删除尝试不得减少历史行"


def test_ack_suppresses_surplus_alert_and_revoke_restores_it(
    client: TestClient, ack_store: PositionAckStore
):
    before = cast(dict[str, Any], client.get("/api/overview").json())
    assert any(
        alert["code"] == "exchange_position_surplus"
        for alert in before["alerts"]
    ), "未标记时必须告警"

    created = cast(dict[str, Any], _create(client).json())
    after = cast(dict[str, Any], client.get("/api/overview").json())
    assert not any(
        alert["code"] == "exchange_position_surplus"
        for alert in after["alerts"]
    ), "标记覆盖全部差额后必须静默"
    summary = after["strategies"][0]
    assert summary["ack"]["external_qty"] == 3000.0

    client.post(f"/api/position-acks/{created['id']}/revoke")
    restored = cast(dict[str, Any], client.get("/api/overview").json())
    assert any(
        alert["code"] == "exchange_position_surplus"
        for alert in restored["alerts"]
    ), "撤销标记后必须恢复告警"
    assert ack_store.count() == 1


def test_ack_routes_require_token(
    reader: OrderJournalReader,
    commands: CommandStore,
    ack_store: PositionAckStore,
    runtime_path: Path,
):
    with TestClient(
        create_app(
            reader,
            commands,
            runtime_path=runtime_path,
            ack_store=ack_store,
            token="secret",
        )
    ) as guarded:
        assert guarded.get("/api/position-acks").status_code == 401
        assert _create(guarded).status_code == 401

        ok = guarded.get(
            "/api/position-acks", headers={"Authorization": "Bearer secret"}
        )
        assert ok.status_code == 200


def test_ack_routes_return_503_without_store(
    reader: OrderJournalReader,
    commands: CommandStore,
    runtime_path: Path,
):
    with TestClient(
        create_app(reader, commands, runtime_path=runtime_path)
    ) as bare:
        assert bare.get("/api/position-acks").status_code == 503
        assert _create(bare).status_code == 503
