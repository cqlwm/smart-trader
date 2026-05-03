import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any

from model import Order, OrderStatus, Symbol

logger = logging.getLogger(__name__)


class OrderRepository(ABC):
    @abstractmethod
    def save(self, order: Order) -> None:
        pass

    @abstractmethod
    def find_by_id(self, order_id: str) -> Order | None:
        pass

    @abstractmethod
    def find_open_orders(self, strategy_id: str | None = None) -> list[Order]:
        pass

    @abstractmethod
    def find_active_orders(self, strategy_id: str | None = None) -> list[Order]:
        pass

    @abstractmethod
    def find_history(self, strategy_id: str | None = None, since: int = 0) -> list[Order]:
        pass

    @abstractmethod
    def find_by_symbol(self, strategy_id: str | None = None, symbol: Symbol | None = None) -> list[Order]:
        pass

    @abstractmethod
    def remove(self, order_id: str) -> None:
        pass


class InMemoryOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._lock = threading.Lock()

    def save(self, order: Order) -> None:
        with self._lock:
            self._orders[order.order_id] = order

    def find_by_id(self, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(order_id)

    def find_open_orders(self, strategy_id: str | None = None) -> list[Order]:
        with self._lock:
            return [
                o for o in self._orders.values()
                if (strategy_id is None or o.strategy_id == strategy_id)
                and OrderStatus.is_open(o.status)
            ]

    def find_active_orders(self, strategy_id: str | None = None) -> list[Order]:
        with self._lock:
            return [
                o for o in self._orders.values()
                if (strategy_id is None or o.strategy_id == strategy_id)
                and not OrderStatus.is_canceled(o.status)
            ]

    def find_history(self, strategy_id: str | None = None, since: int = 0) -> list[Order]:
        with self._lock:
            return sorted(
                [o for o in self._orders.values()
                 if (strategy_id is None or o.strategy_id == strategy_id)
                 and OrderStatus.is_closed(o.status)
                 and o.updated_at >= since],
                key=lambda o: o.updated_at,
            )

    def find_by_symbol(self, strategy_id: str | None = None, symbol: Symbol | None = None) -> list[Order]:
        with self._lock:
            return [
                o for o in self._orders.values()
                if (strategy_id is None or o.strategy_id == strategy_id)
                and (symbol is None or o.symbol == symbol)
            ]

    def remove(self, order_id: str) -> None:
        with self._lock:
            self._orders.pop(order_id, None)


class SqliteOrderRepository(OrderRepository):
    def __init__(self, db_path: str = "data/trading.db") -> None:
        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._init_table()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self) -> None:
        query = """
            CREATE TABLE IF NOT EXISTS trade_orders (
                order_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                symbol_base TEXT NOT NULL,
                symbol_quote TEXT NOT NULL,
                side TEXT NOT NULL,
                position_side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                filled_quantity REAL DEFAULT 0,
                filled_price REAL DEFAULT 0,
                fee REAL DEFAULT 0,
                stop_loss REAL,
                take_profit REAL
            )
        """
        with self._get_connection() as conn:
            conn.execute(query)
            conn.commit()

    def save(self, order: Order) -> None:
        query = """
            INSERT INTO trade_orders (
                order_id, strategy_id, symbol_base, symbol_quote,
                side, position_side, order_type, quantity, price,
                status, created_at, updated_at,
                filled_quantity, filled_price, fee, stop_loss, take_profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status=excluded.status,
                updated_at=excluded.updated_at,
                filled_quantity=excluded.filled_quantity,
                filled_price=excluded.filled_price,
                fee=excluded.fee,
                stop_loss=excluded.stop_loss,
                take_profit=excluded.take_profit
        """
        values = (
            order.order_id, order.strategy_id,
            order.symbol.base, order.symbol.quote,
            order.side.value, order.position_side.value,
            order.order_type, order.quantity, order.price,
            order.status.value, order.created_at, order.updated_at,
            order.filled_quantity, order.filled_price, order.fee,
            order.stop_loss, order.take_profit,
        )
        with self._write_lock:
            try:
                with self._get_connection() as conn:
                    conn.execute(query, values)
                    conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to save order %s: %s", order.order_id, e)
                raise

    def find_by_id(self, order_id: str) -> Order | None:
        query = "SELECT * FROM trade_orders WHERE order_id = ?"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query, (order_id,)).fetchone()
                return self._row_to_order(row) if row else None
        except sqlite3.Error as e:
            logger.error("Failed to find order %s: %s", order_id, e)
            return None

    def find_open_orders(self, strategy_id: str | None = None) -> list[Order]:
        if strategy_id is not None:
            query = "SELECT * FROM trade_orders WHERE strategy_id = ? AND status = ?"
            return self._query_orders(query, (strategy_id, OrderStatus.OPEN.value))
        query = "SELECT * FROM trade_orders WHERE status = ?"
        return self._query_orders(query, (OrderStatus.OPEN.value,))

    def find_active_orders(self, strategy_id: str | None = None) -> list[Order]:
        if strategy_id is not None:
            query = "SELECT * FROM trade_orders WHERE strategy_id = ? AND status != ?"
            return self._query_orders(query, (strategy_id, OrderStatus.CANCELED.value))
        query = "SELECT * FROM trade_orders WHERE status != ?"
        return self._query_orders(query, (OrderStatus.CANCELED.value,))

    def find_history(self, strategy_id: str | None = None, since: int = 0) -> list[Order]:
        if strategy_id is not None:
            query = "SELECT * FROM trade_orders WHERE strategy_id = ? AND status = ? AND updated_at >= ? ORDER BY updated_at"
            return self._query_orders(query, (strategy_id, OrderStatus.CLOSED.value, since))
        query = "SELECT * FROM trade_orders WHERE status = ? AND updated_at >= ? ORDER BY updated_at"
        return self._query_orders(query, (OrderStatus.CLOSED.value, since))

    def find_by_symbol(self, strategy_id: str | None = None, symbol: Symbol | None = None) -> list[Order]:
        conditions = []
        params: list[Any] = []
        if strategy_id is not None:
            conditions.append("strategy_id = ?")
            params.append(strategy_id)
        if symbol is not None:
            conditions.append("symbol_base = ?")
            params.append(symbol.base)
            conditions.append("symbol_quote = ?")
            params.append(symbol.quote)
        query = "SELECT * FROM trade_orders"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return self._query_orders(query, tuple(params))

    def remove(self, order_id: str) -> None:
        query = "DELETE FROM trade_orders WHERE order_id = ?"
        with self._write_lock:
            try:
                with self._get_connection() as conn:
                    conn.execute(query, (order_id,))
                    conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to remove order %s: %s", order_id, e)

    def _query_orders(self, query: str, params: tuple[Any, ...]) -> list[Order]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_order(row) for row in rows if self._row_to_order(row) is not None]  # type: ignore[misc]
        except sqlite3.Error as e:
            logger.error("Failed to query orders: %s", e)
            return []

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order | None:
        try:
            from model import Symbol as Sym, OrderSide as OS, PositionSide as PS, OrderStatus as OSt
            return Order(
                order_id=row['order_id'],
                strategy_id=row['strategy_id'],
                symbol=Sym(base=row['symbol_base'], quote=row['symbol_quote']),
                side=OS(row['side']),
                position_side=PS(row['position_side']),
                order_type=row['order_type'],
                quantity=row['quantity'],
                price=row['price'],
                status=OSt(row['status']),
                created_at=row['created_at'],
                updated_at=row['updated_at'],
                filled_quantity=row['filled_quantity'],
                filled_price=row['filled_price'],
                fee=row['fee'],
                stop_loss=row['stop_loss'],
                take_profit=row['take_profit'],
            )
        except (KeyError, ValueError) as e:
            logger.error("Failed to parse order row: %s", e)
            return None
