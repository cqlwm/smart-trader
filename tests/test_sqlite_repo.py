import os
import sqlite3
import tempfile
import pytest
from persistence.sqlite_repo import SQLiteStrategyRepository
from persistence.database import DatabaseManager

@pytest.fixture
def repo():
    # Use a temporary file for testing
    fd, db_path = tempfile.mkstemp()
    os.close(fd)
    
    # We must explicitly initialize DB Manager with the temp path
    DatabaseManager._instance = None
    
    repo = SQLiteStrategyRepository(db_path=db_path)
    yield repo
    
    DatabaseManager._instance = None
    os.remove(db_path)

def test_save_and_load_strategy_instance(repo):
    repo.save_strategy_instance(
        strategy_id="test_strat_1",
        strategy_type="simple_grid",
        symbol="BTCUSDT",
        config_data='{"grid_num": 10}'
    )
    
    with repo.db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM strategy_instance WHERE strategy_id = 'test_strat_1'")
        row = cursor.fetchone()
        
        assert row is not None
        assert row["strategy_type"] == "simple_grid"
        assert row["symbol"] == "BTCUSDT"
        assert row["config_data"] == '{"grid_num": 10}'

def test_save_and_load_active_orders(repo):
    # First, need a strategy instance due to foreign key constraints
    repo.save_strategy_instance("strat_1", "simple", "BTCUSDT", "{}")
    
    orders = [
        {
            "id": "order_1",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "order_side": "BUY",
            "entry_price": 50000.0,
            "exit_price": 51000.0,
            "quantity": 1.0,
            "entry_order_id": "ext_1",
            "exit_order_id": None,
            "status": "pending",
            "extra_data": {"total_profit": 0.0}
        }
    ]
    
    repo.save_active_orders("strat_1", orders)
    
    loaded_orders = repo.load_active_orders("strat_1")
    assert len(loaded_orders) == 1
    assert loaded_orders[0]["id"] == "order_1"
    assert loaded_orders[0]["entry_price"] == 50000.0
    assert loaded_orders[0]["extra_data"]["total_profit"] == 0.0

def test_append_and_load_trade_history(repo):
    repo.save_strategy_instance("strat_2", "simple", "ETHUSDT", "{}")
    
    trade = {
        "symbol": "ETHUSDT",
        "entry_order_id": "in_1",
        "exit_order_id": "out_1",
        "entry_price": 3000.0,
        "exit_price": 3100.0,
        "quantity": 2.0,
        "profit": 200.0
    }
    
    repo.append_trade_history("strat_2", trade)
    
    history = repo.load_trade_history("strat_2")
    assert len(history) == 1
    assert history[0]["symbol"] == "ETHUSDT"
    assert history[0]["profit"] == 200.0
