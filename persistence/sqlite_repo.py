import json
import logging
import sqlite3
import threading
from typing import Any, List

from persistence.repository import StrategyRepository
from persistence.database import init_db
from persistence.exceptions import DatabaseQueryError

logger = logging.getLogger(__name__)

class SQLiteStrategyRepository(StrategyRepository):
    def __init__(self, db_path: str = "data/trading.db"):
        self.db_manager = init_db(db_path)
        # Using a lock to prevent 'database is locked' during concurrent writes
        self._write_lock = threading.Lock()

    def save_strategy_instance(self, strategy_id: str, strategy_type: str, symbol: str, config_data: str) -> None:
        query = """
            INSERT INTO strategy_instance (strategy_id, strategy_type, symbol, config_data, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(strategy_id) DO UPDATE SET
                config_data=excluded.config_data,
                updated_at=datetime('now')
        """
        self._execute_write(query, (strategy_id, strategy_type, symbol, config_data))

    def save_active_orders(self, strategy_id: str, orders: List[dict[str, Any]]) -> None:
        """
        全量替换某个策略的活跃订单
        """
        with self._write_lock:
            try:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    # 1. 开启事务，首先删除当前策略的所有活跃订单
                    cursor.execute("DELETE FROM grid_orders WHERE strategy_id = ?", (strategy_id,))
                    
                    # 2. 批量插入新订单
                    if orders:
                        insert_query = """
                            INSERT INTO grid_orders (
                                id, strategy_id, symbol, position_side, order_side,
                                entry_price, exit_price, quantity, entry_order_id,
                                exit_order_id, status, extra_data, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """
                        values = []
                        for order in orders:
                            # 提取需要单独存储的字段，其余放入 extra_data
                            extra_data = order.get('extra_data', {})
                            if isinstance(extra_data, dict):
                                extra_data = json.dumps(extra_data)

                            values.append((
                                order['id'],
                                strategy_id,
                                order['symbol'],
                                order['position_side'],
                                order['order_side'],
                                order['entry_price'],
                                order.get('exit_price'),
                                order['quantity'],
                                order.get('entry_order_id'),
                                order.get('exit_order_id'),
                                order['status'],
                                extra_data
                            ))
                        cursor.executemany(insert_query, values)
                    
                    conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to save active orders for {strategy_id}: {e}")
                raise DatabaseQueryError(f"Error saving active orders: {e}")

    def load_active_orders(self, strategy_id: str) -> List[dict[str, Any]]:
        query = "SELECT * FROM grid_orders WHERE strategy_id = ?"
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (strategy_id,))
                rows = cursor.fetchall()
                
                result = []
                for row in rows:
                    order_dict = dict(row)
                    # 恢复 extra_data 为 dict
                    if order_dict.get('extra_data'):
                        try:
                            order_dict['extra_data'] = json.loads(order_dict['extra_data'])
                        except json.JSONDecodeError:
                            order_dict['extra_data'] = {}
                    else:
                        order_dict['extra_data'] = {}
                    result.append(order_dict)
                return result
        except sqlite3.Error as e:
            logger.error(f"Failed to load active orders for {strategy_id}: {e}")
            raise DatabaseQueryError(f"Error loading active orders: {e}")

    def append_trade_history(self, strategy_id: str, trade_record: dict[str, Any]) -> None:
        query = """
            INSERT INTO trade_history (
                strategy_id, symbol, entry_order_id, exit_order_id,
                entry_price, exit_price, quantity, profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = (
            strategy_id,
            trade_record['symbol'],
            trade_record.get('entry_order_id'),
            trade_record.get('exit_order_id'),
            trade_record['entry_price'],
            trade_record['exit_price'],
            trade_record['quantity'],
            trade_record['profit']
        )
        self._execute_write(query, values)

    def load_trade_history(self, strategy_id: str) -> List[dict[str, Any]]:
        query = "SELECT * FROM trade_history WHERE strategy_id = ?"
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (strategy_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to load trade history for {strategy_id}: {e}")
            raise DatabaseQueryError(f"Error loading trade history: {e}")

    def _execute_write(self, query: str, parameters: tuple = ()) -> None:
        with self._write_lock:
            try:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(query, parameters)
                    conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Database write execution failed: {e}, Query: {query}")
                raise DatabaseQueryError(f"Write operation failed: {e}")
