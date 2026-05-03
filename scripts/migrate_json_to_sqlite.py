import os
import glob
import logging
import sys

# Add parent directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.sqlite_repo import SQLiteStrategyRepository
from strategy.simple_grid_strategy import OrderPairListModel, OrderPair
from strategy.signal_grid_strategy import OrderExtensionManager, OrderExtension
from model import OrderSide

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
    """Migrate SignalGridStrategy backup JSON files.

    Note: The old OrderRecorder/Order classes have been replaced by
    OrderExtensionManager/OrderExtension. This migration reads the old
    JSON format (which stored OrderRecorder data) and converts it to
    the new database format.
    """
    file_path = os.path.join(data_dir, "grids_strategy_v2.json")
    if not os.path.exists(file_path):
        logger.info(f"No signal grid backup found at {file_path}")
        return

    try:
        import json
        with open(file_path, 'r') as f:
            raw = json.load(f)

        symbol = "UNKNOWN"
        strategy_id = f"signal_grid_migrated"

        repo.save_strategy_instance(
            strategy_id=strategy_id,
            strategy_type="signal_grid",
            symbol=symbol,
            config_data="{}"
        )

        old_orders = raw.get('orders', [])
        db_orders = []
        for o in old_orders:
            ext = OrderExtension(
                entry_id=o.get('entry_id', ''),
                side=OrderSide(o.get('side', 'buy')),
                price=o.get('price', 0.0),
                quantity=o.get('quantity', 0.0),
                fixed_take_profit_rate=o.get('fixed_take_profit_rate', 0.0),
                signal_min_take_profit_rate=o.get('signal_min_take_profit_rate', 0.0),
                exit_price=o.get('exit_price'),
                status=o.get('status'),
                exit_id=o.get('exit_id'),
                stop_loss_rate=o.get('stop_loss_rate', 0.0),
                enable_stop_loss=o.get('enable_stop_loss', False),
                trailing_stop_rate=o.get('trailing_stop_rate', 0.0),
                enable_trailing_stop=o.get('enable_trailing_stop', False),
                trailing_stop_activation_profit_rate=o.get('trailing_stop_activation_profit_rate', 0.0),
                current_stop_price=o.get('current_stop_price'),
            )
            db_orders.append(ext.to_db_dict(symbol))

        repo.save_active_orders(strategy_id, db_orders)

        # history orders
        old_history = raw.get('history_orders', [])
        for o in old_history:
            direction = 1 if o.get('side', 'buy') == "buy" else -1
            profit = 0.0
            exit_price = o.get('exit_price')
            price = o.get('price', 0.0)
            if exit_price and price:
                profit = (exit_price - price) * o.get('quantity', 0.0) * direction

            repo.append_trade_history(
                strategy_id=strategy_id,
                trade_record={
                    'symbol': symbol,
                    'entry_order_id': o.get('entry_id', ''),
                    'exit_order_id': o.get('exit_id', ''),
                    'entry_price': price,
                    'exit_price': exit_price or 0.0,
                    'quantity': o.get('quantity', 0.0),
                    'profit': profit
                }
            )

        logger.info(f"Successfully migrated {file_path} ({len(db_orders)} active, {len(old_history)} history)")
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
