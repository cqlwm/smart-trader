import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import get_bot_manager

client = TestClient(app)

class MockBotManager:
    class MockClient:
        class MockExchange:
            def fetch_balance(self):
                return {
                    "info": {}, 
                    "USDT": {"free": 1000.0, "used": 50.0},
                    "BTC": {"free": 0.0, "used": 0.0}
                }
        exchange = MockExchange()
        
        def positions(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "contracts": 0.5,
                    "side": "long",
                    "entryPrice": 60000.0,
                    "unrealizedPnl": 500.0,
                    "leverage": 10
                },
                {
                    "symbol": "ETHUSDT",
                    "contracts": 0.0,
                    "side": "long",
                    "entryPrice": 0.0,
                    "unrealizedPnl": 0.0,
                    "leverage": 10
                }
            ]
            
    main_binance_client = MockClient()

@pytest.fixture(autouse=True)
def setup_overrides():
    def override_get_bot_manager():
        return MockBotManager()

    app.dependency_overrides[get_bot_manager] = override_get_bot_manager
    yield
    app.dependency_overrides = {}

def test_get_balances(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "test_key")
    
    response = client.get("/api/v1/account/balances", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    # Should only return non-zero balances
    assert len(data["data"]) == 1
    assert data["data"][0]["asset"] == "USDT"
    assert data["data"][0]["free"] == 1000.0

def test_get_positions(monkeypatch):
    from api import dependencies
    monkeypatch.setattr(dependencies, "API_KEY", "test_key")
    
    response = client.get("/api/v1/account/positions", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    # Should only return positions with contracts > 0
    assert len(data["data"]) == 1
    assert data["data"][0]["symbol"] == "BTCUSDT"
    assert data["data"][0]["positionAmt"] == 0.5

