import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import get_bot_manager

client = TestClient(app)

class MockSymbol:
    def binance(self):
        return "BTCUSDT"

class MockStrategy:
    symbols = [MockSymbol()]
    timeframes = ["1h", "4h"]

class MockHandler:
    strategy = MockStrategy()

class MockEventLoop:
    handlers = [MockHandler()]

class MockThread:
    def is_alive(self):
        return True

class MockBotManager:
    data_event_loop = MockEventLoop()
    _thread = MockThread()

@pytest.fixture(autouse=True)
def setup_overrides():
    def override_get_bot_manager():
        return MockBotManager()

    app.dependency_overrides[get_bot_manager] = override_get_bot_manager
    yield
    app.dependency_overrides = {}

def test_get_strategies(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "test_key")
    
    response = client.get("/api/v1/strategies", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["is_running"] is True
    assert len(data["data"]["strategies"]) == 1
    
    strategy = data["data"]["strategies"][0]
    assert strategy["name"] == "MockStrategy"
    assert strategy["symbols"] == ["BTCUSDT"]
    assert strategy["timeframes"] == ["1h", "4h"]

