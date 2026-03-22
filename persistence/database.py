import sqlite3
import threading
import logging
from typing import Optional
from persistence.exceptions import DatabaseConnectionError

logger = logging.getLogger(__name__)

class DatabaseManager:
    _instance: Optional['DatabaseManager'] = None
    _lock = threading.Lock()
    db_path: str

    def __new__(cls, db_path: str = "data/trading.db"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabaseManager, cls).__new__(cls)
                cls._instance.db_path = db_path
                cls._instance._init_db()
            return cls._instance

    def _init_db(self) -> None:
        """Initialize the database schema."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Create strategy_instance table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_instance (
                        strategy_id TEXT PRIMARY KEY,
                        strategy_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        config_data TEXT,
                        status TEXT DEFAULT 'running',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create grid_orders table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS grid_orders (
                        id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        position_side TEXT NOT NULL,
                        order_side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL,
                        quantity REAL NOT NULL,
                        entry_order_id TEXT,
                        exit_order_id TEXT,
                        status TEXT NOT NULL,
                        extra_data TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (strategy_id) REFERENCES strategy_instance (strategy_id)
                    )
                """)

                # Create trade_history table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        entry_order_id TEXT,
                        exit_order_id TEXT,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        profit REAL NOT NULL,
                        closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (strategy_id) REFERENCES strategy_instance (strategy_id)
                    )
                """)

                conn.commit()
                logger.info(f"Database initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")
            raise DatabaseConnectionError(f"Database initialization failed: {e}")

    def get_connection(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        try:
            # check_same_thread=False is needed because we might use connections across threads
            # but we will manage concurrent writes using locks or SQLite's internal mechanisms
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row  # Return rows as dictionaries
            return conn
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise DatabaseConnectionError(f"Database connection failed: {e}")

def init_db(db_path: str = "data/trading.db") -> DatabaseManager:
    """Helper function to initialize and return the DatabaseManager."""
    return DatabaseManager(db_path)
