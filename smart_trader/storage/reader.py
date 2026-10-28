"""只读账本仓储（API / 报表进程）

与 `OrderJournal` 共享 `queries.py` 的 SELECT 语义与 `engine.py` 的结构校验,
但**不建表、不暴露任何写方法、不调交易所**; 打开前要求库文件已存在,
避免只读进程顺手创建空库。
"""

from pathlib import Path

from smart_trader.model import OrderStatus
from smart_trader.storage import queries
from smart_trader.storage.engine import create_engine_for_path, require_current_schema
from smart_trader.storage.records import GridPositionRow, OrderRecord


class OrderJournalReader:
    """只读账本: 持仓 / 订单 / 策略键查询"""

    def __init__(self, db_path: str):
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(f"账本库不存在, 只读仓储不会创建: {db_path}")
        self._db_path = str(path)
        self._engine = create_engine_for_path(self._db_path)
        with self._engine.connect() as conn:
            require_current_schema(conn)

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        self._engine.dispose()

    def load_positions(
        self,
        *,
        strategy_key: str | None = None,
        include_closed: bool = False,
    ) -> list[GridPositionRow]:
        """仓位行: 默认只取活跃仓位; include_closed=True 含归档"""
        with self._engine.connect() as conn:
            return queries.select_positions(
                conn, strategy_key=strategy_key, include_closed=include_closed
            )

    def load_positions_by_ids(self, position_ids: list[str]) -> list[GridPositionRow]:
        """按仓位号加载（含归档）"""
        with self._engine.connect() as conn:
            return queries.select_positions_by_ids(conn, position_ids)

    def load_orders_for_positions(self, position_ids: list[str]) -> list[OrderRecord]:
        """若干逻辑仓位的全部订单行"""
        with self._engine.connect() as conn:
            return queries.select_orders_for_positions(conn, position_ids)

    def query_orders(
        self,
        *,
        strategy_key: str | None = None,
        status: OrderStatus | None = None,
        unconfirmed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OrderRecord]:
        """订单分页: unconfirmed=True 映射 status IS NULL"""
        with self._engine.connect() as conn:
            return queries.select_orders(
                conn,
                strategy_key=strategy_key,
                status=status,
                unconfirmed=unconfirmed,
                limit=limit,
                offset=offset,
            )

    def count_orders(
        self,
        *,
        strategy_key: str | None = None,
        status: OrderStatus | None = None,
        unconfirmed: bool = False,
    ) -> int:
        """订单总数（与 query_orders 同口径）"""
        with self._engine.connect() as conn:
            return queries.count_orders(
                conn,
                strategy_key=strategy_key,
                status=status,
                unconfirmed=unconfirmed,
            )

    def list_strategy_keys(self) -> list[str]:
        """账本中出现过的全部策略键"""
        with self._engine.connect() as conn:
            return queries.select_strategy_keys(conn)
