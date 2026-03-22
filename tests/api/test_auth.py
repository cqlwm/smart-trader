import os
import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import get_bot_manager

client = TestClient(app)

class MockBotManager:
    class MockClient:
        class MockExchange:
            def fetch_balance(self):
                return {"info": {}, "USDT": {"free": 100, "used": 0}}
        exchange = MockExchange()
    main_binance_client = MockClient()

@pytest.fixture(autouse=True)
def setup_overrides():
    def override_get_bot_manager():
        return MockBotManager()

    app.dependency_overrides[get_bot_manager] = override_get_bot_manager
    yield
    app.dependency_overrides = {}

def test_missing_api_key():
    response = client.get("/api/v1/account/balances")
    assert response.status_code == 422 # RequestValidationError because Header is missing

def test_invalid_api_key(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "real_key")
    response = client.get("/api/v1/account/balances", headers={"X-API-Key": "invalid_key_123"})
    assert response.status_code == 401
    assert response.json()["message"] == "Unauthorized"


def test_valid_api_key(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "test_key")
    
    response = client.get("/api/v1/account/balances", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0


