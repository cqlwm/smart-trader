"""订单日志（sqlite）

三张表:

- `order_ids`（WAL）: 下单前先预留订单号（used=0 + 意图快照）,
  挂单成功后与仓位/流水同事务核销; 明确拒绝直接核销;
  结果未知保持 used=0, 由对账器去交易所确认。语义见 .memory/01-system-architecture.md 第十一节。
- `grid_positions`: 逻辑仓位（GridPosition）的持久化, 以 strategy_key 归属, active 标记当前/归档。
- `orders`: 订单流水, 一次"交易所确认存在过"的委托一行（append 为主, 被拒尝试只留在 id 表）。
"""

import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.model import OrderSide, OrderStatus, OrderType, Symbol

logger = log.getLogger(__name__)


class OrderRole(Enum):
    """下单意图 / 订单流水角色"""

    ENTRY = "entry"
    EXIT = "exit"


class GridPositionRow(BaseModel):
    """grid_positions 表的一行, `GridPosition`（逻辑仓位）的持久化形态"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    strategy_key: str
    side: OrderSide
    price: float
    quantity: float
    filled_quantity: float = 0.0
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    exit_price: float | None = None
    status: OrderStatus | None = None
    exit_id: str | None = None

    # 止损字段
    stop_loss_rate: float = 0.0
    enable_stop_loss: bool = False
    trailing_stop_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_activation_profit_rate: float = 0.0
    current_stop_price: float | None = None

    active: bool = True
    created_at: str = ""
    updated_at: str = ""


class OrderRecord(BaseModel):
    """orders 表的一行: 一次真实委托的流水记录（审计/统计）"""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    strategy_key: str
    role: OrderRole
    symbol: str
    side: OrderSide
    type: OrderType
    price: float | None = None
    quantity: float = 0.0
    filled_qty: float = 0.0
    avg_price: float | None = None
    status: OrderStatus
    created_at: str = ""
    updated_at: str = ""


class OrderIntent(BaseModel):
    """下单意图快照: 崩溃后对账重建仓位与流水所需的最小上下文"""

    model_config = ConfigDict(extra="forbid")

    kind: OrderRole
    symbol: Symbol
    side: OrderSide
    # 网格下单恒为 LIMIT; 保留字段供对账重建流水使用
    order_type: OrderType = OrderType.LIMIT
    # kind=ENTRY: 重建仓位行的模板（策略参数来自这里）
    entry_position: GridPositionRow | None = None
    # kind=EXIT: 该出场单关联的入场单号
    entry_ids: list[str] = []


class IdRecord(BaseModel):
    """order_ids 表的一行"""

    model_config = ConfigDict(extra="forbid")

    id: str
    used: bool
    type: str
    strategy_key: str
    intent: OrderIntent
    created_at: str
    updated_at: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


_CREATE_ORDER_IDS = """
CREATE TABLE IF NOT EXISTS order_ids (
    id TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL DEFAULT 'ORDER',
    strategy_key TEXT NOT NULL,
    intent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_ids_pending ON order_ids(strategy_key, used);
"""

_CREATE_GRID_POSITIONS = """
CREATE TABLE IF NOT EXISTS grid_positions (
    entry_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    filled_quantity REAL NOT NULL DEFAULT 0,
    fixed_take_profit_rate REAL NOT NULL,
    signal_min_take_profit_rate REAL NOT NULL,
    exit_price REAL,
    status TEXT,
    exit_id TEXT,
    stop_loss_rate REAL NOT NULL DEFAULT 0,
    enable_stop_loss INTEGER NOT NULL DEFAULT 0,
    trailing_stop_rate REAL NOT NULL DEFAULT 0,
    enable_trailing_stop INTEGER NOT NULL DEFAULT 0,
    trailing_stop_activation_profit_rate REAL NOT NULL DEFAULT 0,
    current_stop_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grid_positions_strategy ON grid_positions(strategy_key, active);
"""

_CREATE_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    role TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    price REAL,
    quantity REAL NOT NULL DEFAULT 0,
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_price REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_key, role);
"""

_POSITION_COLUMNS = (
    "entry_id",
    "strategy_key",
    "side",
    "price",
    "quantity",
    "filled_quantity",
    "fixed_take_profit_rate",
    "signal_min_take_profit_rate",
    "exit_price",
    "status",
    "exit_id",
    "stop_loss_rate",
    "enable_stop_loss",
    "trailing_stop_rate",
    "enable_trailing_stop",
    "trailing_stop_activation_profit_rate",
    "current_stop_price",
    "active",
    "created_at",
    "updated_at",
)

_UPSERT_POSITION = f"""
INSERT INTO grid_positions ({", ".join(_POSITION_COLUMNS)})
VALUES ({", ".join("?" for _ in _POSITION_COLUMNS)})
ON CONFLICT(entry_id) DO UPDATE SET
    strategy_key=excluded.strategy_key,
    side=excluded.side,
    price=excluded.price,
    quantity=excluded.quantity,
    filled_quantity=excluded.filled_quantity,
    fixed_take_profit_rate=excluded.fixed_take_profit_rate,
    signal_min_take_profit_rate=excluded.signal_min_take_profit_rate,
    exit_price=excluded.exit_price,
    status=excluded.status,
    exit_id=excluded.exit_id,
    stop_loss_rate=excluded.stop_loss_rate,
    enable_stop_loss=excluded.enable_stop_loss,
    trailing_stop_rate=excluded.trailing_stop_rate,
    enable_trailing_stop=excluded.enable_trailing_stop,
    trailing_stop_activation_profit_rate=excluded.trailing_stop_activation_profit_rate,
    current_stop_price=excluded.current_stop_price,
    active=excluded.active,
    updated_at=excluded.updated_at
"""

_ORDER_RECORD_COLUMNS = (
    "order_id",
    "strategy_key",
    "role",
    "symbol",
    "side",
    "type",
    "price",
    "quantity",
    "filled_qty",
    "avg_price",
    "status",
    "created_at",
    "updated_at",
)

_UPSERT_ORDER_RECORD = f"""
INSERT INTO orders ({", ".join(_ORDER_RECORD_COLUMNS)})
VALUES ({", ".join("?" for _ in _ORDER_RECORD_COLUMNS)})
ON CONFLICT(order_id) DO UPDATE SET
    strategy_key=excluded.strategy_key,
    role=excluded.role,
    symbol=excluded.symbol,
    side=excluded.side,
    type=excluded.type,
    price=excluded.price,
    quantity=excluded.quantity,
    filled_qty=excluded.filled_qty,
    avg_price=excluded.avg_price,
    status=excluded.status,
    updated_at=excluded.updated_at
"""

_MAX_ID_RETRY = 5


class OrderJournal:
    """订单日志: id 表（WAL）+ 逻辑仓位表 + 订单流水表 + 事务, 线程安全"""

    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._migrate_legacy_schema()
            self._conn.executescript(_CREATE_ORDER_IDS)
            self._conn.executescript(_CREATE_GRID_POSITIONS)
            self._conn.executescript(_CREATE_ORDERS)

    def close(self) -> None:
        self._conn.close()

    def _migrate_legacy_schema(self) -> None:
        """
        兼容旧库（Step 8 之前）: 当时的 orders 表承载的是逻辑仓位（无 role 列）。

        迁移动作: orders 改名 grid_positions; 旧意图快照 `entry_order` 字段改名为
        `entry_position`（仅字符串替换 id 表内的 JSON）。对全新库与非旧结构库幂等无感。
        """
        cursor = self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row["name"] for row in cursor.fetchall()}
        if "orders" not in tables:
            return

        cursor = self._conn.execute("PRAGMA table_info(orders)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "role" in columns:
            return  # 已是新版订单流水表

        if "grid_positions" in tables:
            raise RuntimeError(
                "检测到旧版 orders（逻辑仓位）与新 grid_positions 并存, 数据状态不明确, 请人工处理"
            )

        logger.info("检测到旧版 orders 表（逻辑仓位形态）, 迁移为 grid_positions")
        self._conn.execute("ALTER TABLE orders RENAME TO grid_positions")
        if "order_ids" in tables:
            self._conn.execute(
                "UPDATE order_ids SET intent = REPLACE("
                "intent, '\"entry_order\"', '\"entry_position\"')"
            )

    # ---- id 表（WAL） ----

    def reserve(
        self,
        strategy_key: str,
        intent: OrderIntent,
        preferred_id: str | None = None,
    ) -> str:
        """
        预留一个订单号: 写入 id 表（used=0 + 意图快照）, 返回 custom_id

        @param strategy_key: 归属策略
        @param intent: 意图快照（对账重建仓位与流水所需上下文）
        @param preferred_id: 期望使用的订单号; 被占用时自动换新
        """
        with self._lock:
            for attempt in range(_MAX_ID_RETRY):
                custom_id = (
                    preferred_id
                    if attempt == 0 and preferred_id
                    else self._generate_id(intent.side)
                )
                now = _now()
                try:
                    with self._conn:
                        self._conn.execute(
                            "INSERT INTO order_ids"
                            " (id, used, type, strategy_key, intent, created_at, updated_at)"
                            " VALUES (?, 0, 'ORDER', ?, ?, ?, ?)",
                            (
                                custom_id,
                                strategy_key,
                                intent.model_dump_json(),
                                now,
                                now,
                            ),
                        )
                    return custom_id
                except sqlite3.IntegrityError:
                    continue
            raise RuntimeError(f"连续 {_MAX_ID_RETRY} 次未能生成唯一订单号")

    def pending_ids(self, strategy_key: str) -> list[IdRecord]:
        """未确认（待对账）的 id 列表"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM order_ids WHERE strategy_key = ? AND used = 0"
                " ORDER BY created_at",
                (strategy_key,),
            )
            return [self._to_id_record(row) for row in cursor.fetchall()]

    def void(self, custom_id: str) -> None:
        """明确失败: 核销订单号, 不留仓位与流水"""
        with self._lock:
            self._settle(custom_id)

    # ---- 写（挂上成功: 仓位 + 流水 + 核销, 同一事务） ----

    def confirm_entry(
        self,
        custom_id: str,
        position: GridPositionRow,
        order: OrderRecord,
    ) -> None:
        """入场挂上成功: 同一事务写入/更新仓位行、记录订单流水并核销订单号"""
        with self._lock, self._conn:
            self._settle(custom_id)
            self._upsert_position(position)
            self._upsert_order_record(order)

    def confirm_exit(
        self,
        custom_id: str,
        entry_ids: list[str],
        exit_id: str,
        exit_price: float,
        order: OrderRecord,
    ) -> None:
        """出场单挂上: 同一事务回填各仓位出场信息、记录订单流水并核销订单号"""
        with self._lock, self._conn:
            self._settle(custom_id)
            record = self._get_id_record(custom_id)
            now = _now()
            for entry_id in entry_ids:
                self._conn.execute(
                    "UPDATE grid_positions SET exit_id = ?, exit_price = ?, updated_at = ?"
                    " WHERE entry_id = ? AND strategy_key = ?",
                    (exit_id, exit_price, now, entry_id, record.strategy_key),
                )
            self._upsert_order_record(order)

    # ---- 逻辑仓位表 ----

    def load_positions(self, strategy_key: str) -> list[GridPositionRow]:
        """加载策略当前仓位（active=1）"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM grid_positions WHERE strategy_key = ? AND active = 1"
                " ORDER BY rowid",
                (strategy_key,),
            )
            return [self._to_position_row(row) for row in cursor.fetchall()]

    def load_archived_positions(self, strategy_key: str) -> list[GridPositionRow]:
        """加载已归档仓位（active=0, 最近归档在前）—— 用于对账/盈亏统计"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM grid_positions WHERE strategy_key = ? AND active = 0"
                " ORDER BY updated_at DESC, rowid DESC",
                (strategy_key,),
            )
            return [self._to_position_row(row) for row in cursor.fetchall()]

    def deactivate_position(self, entry_id: str) -> None:
        """归档仓位（已平仓）: 保留行, 置 active=0"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE grid_positions SET active = 0, updated_at = ? WHERE entry_id = ?",
                (_now(), entry_id),
            )

    def sync_positions(
        self,
        strategy_key: str,
        rows: list[GridPositionRow],
        removed_ids: list[str],
    ) -> None:
        """
        事务同步当前仓位快照: upsert rows + 归档 removed_ids

        @param strategy_key: 归属策略（removed 归档也限定该策略, 防越界）
        @param rows: 当前仓位（快照, 覆盖旧值）
        @param removed_ids: 本次移除的仓位号（置 active=0）
        """
        with self._lock, self._conn:
            for row in rows:
                self._upsert_position(row)
            now = _now()
            for entry_id in removed_ids:
                self._conn.execute(
                    "UPDATE grid_positions SET active = 0, updated_at = ?"
                    " WHERE entry_id = ? AND strategy_key = ?",
                    (now, entry_id, strategy_key),
                )

    # ---- 订单流水表 ----

    def load_order_records(self, strategy_key: str) -> list[OrderRecord]:
        """加载策略的订单流水（写入序）"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM orders WHERE strategy_key = ? ORDER BY created_at, rowid",
                (strategy_key,),
            )
            return [self._to_order_record(row) for row in cursor.fetchall()]

    # ---- 内部工具 ----

    def _generate_id(self, side: OrderSide) -> str:
        return f"{side.value}{secrets.token_hex(nbytes=5)}"

    def _settle(self, custom_id: str) -> None:
        """核销订单号（used=1）; 未登记的订单号视为调用方错误"""
        cursor = self._conn.execute(
            "UPDATE order_ids SET used = 1, updated_at = ? WHERE id = ?",
            (_now(), custom_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"订单号未在 id 表登记, 不能核销: {custom_id}")

    def _get_id_record(self, custom_id: str) -> IdRecord:
        cursor = self._conn.execute(
            "SELECT * FROM order_ids WHERE id = ?", (custom_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"订单号未在 id 表登记: {custom_id}")
        return self._to_id_record(row)

    def _upsert_position(self, row: GridPositionRow) -> None:
        now = _now()
        values = (
            row.entry_id,
            row.strategy_key,
            row.side.value,
            row.price,
            row.quantity,
            row.filled_quantity,
            row.fixed_take_profit_rate,
            row.signal_min_take_profit_rate,
            row.exit_price,
            row.status.value if row.status is not None else None,
            row.exit_id,
            row.stop_loss_rate,
            int(row.enable_stop_loss),
            row.trailing_stop_rate,
            int(row.enable_trailing_stop),
            row.trailing_stop_activation_profit_rate,
            row.current_stop_price,
            int(row.active),
            row.created_at or now,
            now,
        )
        self._conn.execute(_UPSERT_POSITION, values)

    def _upsert_order_record(self, row: OrderRecord) -> None:
        now = _now()
        values = (
            row.order_id,
            row.strategy_key,
            row.role.value,
            row.symbol,
            row.side.value,
            row.type.value,
            row.price,
            row.quantity,
            row.filled_qty,
            row.avg_price,
            row.status.value,
            row.created_at or now,
            now,
        )
        self._conn.execute(_UPSERT_ORDER_RECORD, values)

    def _to_id_record(self, row: sqlite3.Row) -> IdRecord:
        data: dict[str, Any] = dict(row)
        return IdRecord(
            id=data["id"],
            used=bool(data["used"]),
            type=data["type"],
            strategy_key=data["strategy_key"],
            intent=OrderIntent.model_validate_json(data["intent"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    def _to_position_row(self, row: sqlite3.Row) -> GridPositionRow:
        data: dict[str, Any] = dict(row)
        status = data["status"]
        return GridPositionRow(
            entry_id=data["entry_id"],
            strategy_key=data["strategy_key"],
            side=OrderSide(data["side"]),
            price=data["price"],
            quantity=data["quantity"],
            filled_quantity=data["filled_quantity"],
            fixed_take_profit_rate=data["fixed_take_profit_rate"],
            signal_min_take_profit_rate=data["signal_min_take_profit_rate"],
            exit_price=data["exit_price"],
            status=OrderStatus(status) if status is not None else None,
            exit_id=data["exit_id"],
            stop_loss_rate=data["stop_loss_rate"],
            enable_stop_loss=bool(data["enable_stop_loss"]),
            trailing_stop_rate=data["trailing_stop_rate"],
            enable_trailing_stop=bool(data["enable_trailing_stop"]),
            trailing_stop_activation_profit_rate=data[
                "trailing_stop_activation_profit_rate"
            ],
            current_stop_price=data["current_stop_price"],
            active=bool(data["active"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    def _to_order_record(self, row: sqlite3.Row) -> OrderRecord:
        data: dict[str, Any] = dict(row)
        return OrderRecord(
            order_id=data["order_id"],
            strategy_key=data["strategy_key"],
            role=OrderRole(data["role"]),
            symbol=data["symbol"],
            side=OrderSide(data["side"]),
            type=OrderType(data["type"]),
            price=data["price"],
            quantity=data["quantity"],
            filled_qty=data["filled_qty"],
            avg_price=data["avg_price"],
            status=OrderStatus(data["status"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )
