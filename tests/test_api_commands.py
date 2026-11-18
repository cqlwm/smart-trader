"""控制命令 API 测试: 提交 / 鉴权 / 参数校验 / 审计查询 / 端到端执行"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.model import CommandAction, CommandStatus
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.task.command_runner import CommandRunner
from tests.api_harness import AUTH_HEADERS, TEST_TOKEN
from tests.test_command_runner import KEY, FakeControllableStrategy


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


def _body(
    action: str, idempotency_key: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {"action": action}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return payload


def test_submit_pause_command_returns_pending(client: TestClient):
    response = client.post(f"/api/strategies/{KEY}/commands", json=_body("pause"))

    assert response.status_code == 202
    body = cast(dict[str, Any], response.json())
    assert body["id"].startswith("CMD")
    assert body["action"] == "pause"
    assert body["status"] == "pending"
    assert body["strategy_key"] == KEY


def test_submit_kill_switch_is_global(client: TestClient):
    response = client.post("/api/commands/kill-switch", json={})

    assert response.status_code == 202
    body = cast(dict[str, Any], response.json())
    assert body["action"] == "kill_switch"
    assert body["strategy_key"] is None


def test_submit_unknown_action_returns_422(client: TestClient):
    response = client.post(
        f"/api/strategies/{KEY}/commands", json=_body("launch_rocket")
    )

    assert response.status_code == 422


def test_submit_kill_switch_via_strategy_path_returns_400(client: TestClient):
    response = client.post(
        f"/api/strategies/{KEY}/commands", json=_body("kill_switch")
    )

    assert response.status_code == 400
    assert "kill-switch" in cast(dict[str, Any], response.json())["detail"]


def test_idempotency_key_returns_same_command(client: TestClient):
    first = client.post(
        f"/api/strategies/{KEY}/commands",
        json=_body("pause", idempotency_key="ik-1"),
    )
    second = client.post(
        f"/api/strategies/{KEY}/commands",
        json=_body("resume", idempotency_key="ik-1"),
    )

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_list_commands_filters_and_paginates(client: TestClient):
    client.post(f"/api/strategies/{KEY}/commands", json=_body("pause"))
    client.post(f"/api/strategies/{KEY}/commands", json=_body("resume"))
    client.post("/api/commands/kill-switch", json={})

    filtered = client.get("/api/commands", params={"strategy_key": KEY})
    page = client.get("/api/commands", params={"limit": 2, "offset": 0})

    assert filtered.status_code == 200
    assert cast(dict[str, Any], filtered.json())["total"] == 2
    assert page.status_code == 200
    assert len(cast(dict[str, Any], page.json())["items"]) == 2


def test_get_command_returns_404_for_unknown_id(client: TestClient):
    assert client.get("/api/commands/CMDmissing").status_code == 404


def test_command_submission_requires_token(
    reader: OrderJournalReader, commands: CommandStore
):
    with TestClient(create_app(reader, commands, token="secret")) as guarded:
        assert (
            guarded.post(f"/api/strategies/{KEY}/commands", json=_body("pause"))
            .status_code
            == 401
        )
        ok = guarded.post(
            f"/api/strategies/{KEY}/commands",
            json=_body("pause"),
            headers={"Authorization": "Bearer secret"},
        )
        assert ok.status_code == 202


def test_runtime_only_strategy_is_listed(
    reader: OrderJournalReader, commands: CommandStore, tmp_path: Path
):
    runtime_path = tmp_path / "runtime.json"
    RuntimeRegistry(runtime_path).heartbeat([FakeControllableStrategy(KEY)])

    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN, runtime_path=runtime_path),
        headers=AUTH_HEADERS,
    ) as runtime_client:
        response = runtime_client.get("/api/strategies")

    assert response.status_code == 200
    items = cast(list[dict[str, Any]], response.json())
    assert any(
        item["strategy_key"] == KEY and item["alive"] is True for item in items
    ), "尚未写账本的运行中策略必须可见"


def test_submitted_command_is_executed_by_runner(
    client: TestClient, commands: CommandStore
):
    strategy = FakeControllableStrategy(KEY)
    runner = CommandRunner(commands, {KEY: strategy})
    submitted = client.post(
        f"/api/strategies/{KEY}/commands", json=_body("pause")
    )

    assert runner.run_once() is True

    command_id = str(cast(dict[str, Any], submitted.json())["id"])
    loaded = client.get(f"/api/commands/{command_id}")
    assert loaded.status_code == 200
    assert loaded.json()["status"] == CommandStatus.SUCCEEDED.value
    assert strategy.paused is True
    assert loaded.json()["action"] == CommandAction.PAUSE.value
