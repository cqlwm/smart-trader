"""存储层表结构（SQLAlchemy Core Table 元数据）

schema 的**唯一来源**: 列定义、约束、索引都只在这里出现一次;

- 建库走 `metadata.create_all()`;
- `order_journal.py` 的所有语句都引用这里的 table 对象;
- 列类型刻意与旧版 DDL 对齐（TEXT / INTEGER / REAL）, 保证与历史库完全兼容,
  `tests/test_storage_schema.py` 用冻结的旧版 DDL 快照做等价性校验。

有意变更库结构时: 先改本文件 → 同步更新等价性测试的冻结快照 → 评估旧库迁移。
"""

from sqlalchemy import REAL, Column, Index, Integer, MetaData, Table, Text, text

metadata = MetaData()

# ---- order_ids（WAL）: 预生成的订单号 + 确认状态 + 意图快照 ----

order_ids = Table(
    "order_ids",
    metadata,
    Column("id", Text, primary_key=True),
    Column("used", Integer, nullable=False, server_default=text("0")),
    Column("type", Text, nullable=False, server_default=text("'ORDER'")),
    Column("strategy_key", Text, nullable=False),
    Column("intent", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index("idx_order_ids_pending", order_ids.c.strategy_key, order_ids.c.used)

# ---- grid_positions: 逻辑仓位（GridPosition）的持久化 ----

grid_positions = Table(
    "grid_positions",
    metadata,
    Column("entry_id", Text, primary_key=True),
    Column("strategy_key", Text, nullable=False),
    Column("side", Text, nullable=False),
    Column[float]("price", REAL, nullable=False),
    Column[float]("quantity", REAL, nullable=False),
    Column[float]("filled_quantity", REAL, nullable=False, server_default=text("0")),
    Column[float]("fixed_take_profit_rate", REAL, nullable=False),
    Column[float]("signal_min_take_profit_rate", REAL, nullable=False),
    Column[float]("exit_price", REAL),
    Column("status", Text),
    Column("exit_id", Text),
    Column[float]("stop_loss_rate", REAL, nullable=False, server_default=text("0")),
    Column("enable_stop_loss", Integer, nullable=False, server_default=text("0")),
    Column[float]("trailing_stop_rate", REAL, nullable=False, server_default=text("0")),
    Column("enable_trailing_stop", Integer, nullable=False, server_default=text("0")),
    Column[float](
        "trailing_stop_activation_profit_rate",
        REAL,
        nullable=False,
        server_default=text("0"),
    ),
    Column[float]("current_stop_price", REAL),
    Column("active", Integer, nullable=False, server_default=text("1")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index(
    "idx_grid_positions_strategy",
    grid_positions.c.strategy_key,
    grid_positions.c.active,
)

# ---- orders: 订单流水（一次"交易所确认存在过"的委托一行, append 为主） ----

orders = Table(
    "orders",
    metadata,
    Column("order_id", Text, primary_key=True),
    Column("strategy_key", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("side", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column[float]("price", REAL),
    Column[float]("quantity", REAL, nullable=False, server_default=text("0")),
    Column[float]("filled_qty", REAL, nullable=False, server_default=text("0")),
    Column[float]("avg_price", REAL),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index("idx_orders_strategy", orders.c.strategy_key, orders.c.role)
