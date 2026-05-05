import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import get_instance_manager, get_bot_manager
from strategy.registry import StrategyRegistry
from strategy.instance_manager import StrategyInstanceManager


class DummyStrategy:
    pass


class DummyConfig:
    pass


@pytest.fixture(autouse=True)
def setup_overrides():
    StrategyRegistry.clear()
    StrategyRegistry.register("dummy", DummyStrategy, DummyConfig)

    mgr = StrategyInstanceManager()

    class MockBotManager:
        data_event_loop = None
        _thread = None
        instance_manager = mgr

    app.dependency_overrides[get_instance_manager] = lambda: mgr
    app.dependency_overrides[get_bot_manager] = lambda: MockBotManager()
    yield
    app.dependency_overrides = {}
    StrategyRegistry.clear()


@pytest.fixture
def api_client(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "test_key")
    return TestClient(app)


HEADERS = {"X-API-Key": "test_key"}


class TestStrategyTypesEndpoint:
    def test_list_strategy_types(self, api_client):
        resp = api_client.get("/api/v1/strategy-types", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert any(t["name"] == "dummy" for t in data)


class TestStrategyCRUD:
    def test_create_strategy(self, api_client):
        resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy",
            "config": {"param": 42},
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["strategy_type"] == "dummy"
        assert data["status"] == "pending"
        assert data["config"] == {"param": 42}
        assert data["instance_id"] is not None

    def test_create_unknown_type_returns_400(self, api_client):
        resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "nonexistent",
            "config": {},
        })
        assert resp.status_code == 400

    def test_list_strategies(self, api_client):
        api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {"a": 1},
        })
        api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {"b": 2},
        })
        resp = api_client.get("/api/v1/strategies", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    def test_get_strategy_detail(self, api_client):
        create_resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {},
        })
        instance_id = create_resp.json()["data"]["instance_id"]

        resp = api_client.get(f"/api/v1/strategies/{instance_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["data"]["instance_id"] == instance_id

    def test_get_nonexistent_returns_404(self, api_client):
        resp = api_client.get("/api/v1/strategies/nonexistent", headers=HEADERS)
        assert resp.status_code == 404

    def test_start_strategy(self, api_client):
        create_resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {},
        })
        instance_id = create_resp.json()["data"]["instance_id"]

        resp = api_client.post(f"/api/v1/strategies/{instance_id}/start", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "running"

    def test_stop_strategy(self, api_client):
        create_resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {},
        })
        instance_id = create_resp.json()["data"]["instance_id"]
        api_client.post(f"/api/v1/strategies/{instance_id}/start", headers=HEADERS)

        resp = api_client.post(f"/api/v1/strategies/{instance_id}/stop", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "stopped"

    def test_delete_strategy(self, api_client):
        create_resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {},
        })
        instance_id = create_resp.json()["data"]["instance_id"]

        resp = api_client.delete(f"/api/v1/strategies/{instance_id}", headers=HEADERS)
        assert resp.status_code == 200

        resp = api_client.get(f"/api/v1/strategies/{instance_id}", headers=HEADERS)
        assert resp.status_code == 404

    def test_delete_running_returns_400(self, api_client):
        create_resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {},
        })
        instance_id = create_resp.json()["data"]["instance_id"]
        api_client.post(f"/api/v1/strategies/{instance_id}/start", headers=HEADERS)

        resp = api_client.delete(f"/api/v1/strategies/{instance_id}", headers=HEADERS)
        assert resp.status_code == 400


class TestFullLifecycle:
    def test_create_start_stop_delete(self, api_client):
        resp = api_client.post("/api/v1/strategies", headers=HEADERS, json={
            "strategy_type": "dummy", "config": {"key": "value"},
        })
        assert resp.status_code == 200
        instance_id = resp.json()["data"]["instance_id"]
        assert resp.json()["data"]["status"] == "pending"

        resp = api_client.post(f"/api/v1/strategies/{instance_id}/start", headers=HEADERS)
        assert resp.json()["data"]["status"] == "running"

        resp = api_client.post(f"/api/v1/strategies/{instance_id}/stop", headers=HEADERS)
        assert resp.json()["data"]["status"] == "stopped"

        resp = api_client.delete(f"/api/v1/strategies/{instance_id}", headers=HEADERS)
        assert resp.status_code == 200

        resp = api_client.get("/api/v1/strategies", headers=HEADERS)
        assert len(resp.json()["data"]) == 0
