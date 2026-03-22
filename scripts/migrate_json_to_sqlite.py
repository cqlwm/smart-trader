import os
import glob
import logging
import sys

# Add parent directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.sqlite_repo import SQLiteStrategyRepository
from strategy.simple_grid_strategy import OrderPairListModel, OrderPair
from strategy.signal_grid_strategy import OrderRecorder, Order

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def migrate_simple_grid_backups(data_dir: str, repo: SQLiteStrategyRepository):
    """Migrate SimpleGridStrategy backup JSON files."""
    pattern = os.path.join(data_dir, "backup_*.json")
    files = glob.glob(pattern)
    
    for file_path in files:
        filename = os.path.basename(file_path)
        # Parse symbol, position_side, order_side from filename
        # e.g., backup_BTCUSDT_LONG_BUY.json
        parts = filename.replace("backup_", "").replace(".json", "").split("_")
        if len(parts) >= 3:
            symbol = parts[0]
            position_side = parts[1]
            order_side = parts[2]
            strategy_id = f"simple_grid_{symbol}_{position_side}_{order_side}"
            
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                    data = OrderPairListModel.model_validate_json(content)
                
                # Save strategy instance dummy
                repo.save_strategy_instance(
                    strategy_id=strategy_id,
                    strategy_type="simple_grid",
                    symbol=symbol,
                    config_data="{}"
                )
                
                db_orders = [grid.to_db_dict() for grid in data.items]
                repo.save_active_orders(strategy_id, db_orders)
                logger.info(f"Successfully migrated {file_path} to {strategy_id} ({len(db_orders)} orders)")
            except Exception as e:
                logger.error(f"Failed to migrate {file_path}: {e}")
        else:
            logger.warning(f"Could not parse strategy parameters from filename: {filename}")

def migrate_signal_grid_backups(data_dir: str, repo: SQLiteStrategyRepository):
    """Migrate SignalGridStrategy backup JSON files."""
    file_path = os.path.join(data_dir, "grids_strategy_v2.json")
    if not os.path.exists(file_path):
        logger.info(f"No signal grid backup found at {file_path}")
        return
        
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            data = OrderRecorder.model_validate_json(content)
            
        # Since the JSON doesn't contain symbol/strategy_id natively, we use a generic one
        # or we might need to deduce it if possible. Let's use a placeholder symbol.
        symbol = "UNKNOWN"
        strategy_id = f"signal_grid_migrated"
        
        repo.save_strategy_instance(
            strategy_id=strategy_id,
            strategy_type="signal_grid",
            symbol=symbol,
            config_data="{}"
        )
        
        db_orders = [o.to_db_dict(symbol) for o in data.orders]
        repo.save_active_orders(strategy_id, db_orders)
        
        # history orders
        for o in data.history_orders:
            direction = 1 if o.side.value == "BUY" else -1
            profit = 0.0
            if o.exit_price and o.price:
                profit = (o.exit_price - o.price) * o.quantity * direction
                
            repo.append_trade_history(
                strategy_id=strategy_id,
                trade_record={
                    'symbol': symbol,
                    'entry_order_id': o.entry_id,
                    'exit_order_id': o.exit_id,
                    'entry_price': o.price,
                    'exit_price': o.exit_price or 0.0,
                    'quantity': o.quantity,
                    'profit': profit
                }
            )
            
        logger.info(f"Successfully migrated {file_path} ({len(db_orders)} active, {len(data.history_orders)} history)")
    except Exception as e:
        logger.error(f"Failed to migrate {file_path}: {e}")

def main():
    data_dir = "data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        logger.info(f"Created data directory at {data_dir}")
        
    db_path = os.path.join(data_dir, "trading.db")
    repo = SQLiteStrategyRepository(db_path=db_path)
    
    logger.info("Starting migration...")
    migrate_simple_grid_backups(data_dir, repo)
    migrate_signal_grid_backups(data_dir, repo)
    logger.info("Migration completed.")

if __name__ == "__main__":
    main()
