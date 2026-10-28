"""存储层表结构（SQLAlchemy Core Table 元数据）

schema 的**唯一来源**: 列定义、约束、索引都只在这里出现一次;

- 建库走 `metadata.create_all()`;
- `order_journal.py` 的所有语句都引用这里的 table 对象;
- 列类型刻意与 DDL 对齐（TEXT / INTEGER / REAL）,
  `tests/test_storage_schema.py` 用冻结的 DDL 快照做等价性校验。

有意变更库结构时: 先改本文件 → 同步更新等价性测试的冻结快照。
"""

from sqlalchemy import REAL, Column, Index, Integer, MetaData, Table, Text, text

metadata = MetaData()

# ---- grid_positions: 逻辑仓位（GridPosition）的策略参数与运行时状态 ----
# 价格 / 数量 / 相位由 orders 派生, 不落库; 唯一物化的状态标记是 active。

grid_positions = Table(
    "grid_positions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("strategy_key", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("position_side", Text, nullable=False),
    Column("main_order_side", Text, nullable=False),
    Column[float]("opening_quantity", REAL, nullable=False, server_default=text("0")),
    Column[float]("opening_price", REAL, nullable=False, server_default=text("0")),
    Column[float]("fixed_take_profit_rate", REAL, nullable=False),
    Column[float]("signal_min_take_profit_rate", REAL, nullable=False),
    Column("enable_stop_loss", Integer, nullable=False, server_default=text("0")),
    Column[float]("stop_loss_rate", REAL, nullable=False, server_default=text("0")),
    Column("enable_trailing_stop", Integer, nullable=False, server_default=text("0")),
    Column[float]("trailing_stop_rate", REAL, nullable=False, server_default=text("0")),
    Column[float](
        "trailing_stop_activation_rate", REAL, nullable=False, server_default=text("0")
    ),
    Column[float]("trailing_stop_price", REAL),
    Column("active", Integer, nullable=False, server_default=text("1")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    # 审计列必须排在表尾: 存量库迁移用 ALTER TABLE ADD COLUMN(只能追加),
    # 建列位置与冻结契约一致才能通过结构等价测试
    Column("closed_reason", Text),
)
Index(
    "idx_grid_positions_strategy",
    grid_positions.c.strategy_key,
    grid_positions.c.active,
)

# ---- orders: 订单流水（先落行、再发交易所） ----
# `status` 为空 = 结果未确认, 承担 WAL 标记与对账线索。

orders = Table(
    "orders",
    metadata,
    Column("order_id", Text, primary_key=True),
    Column("grid_position_id", Text, nullable=False),
    Column("strategy_key", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("side", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column[float]("price", REAL),
    Column[float]("quantity", REAL, nullable=False, server_default=text("0")),
    Column[float]("filled_qty", REAL, nullable=False, server_default=text("0")),
    Column[float]("avg_price", REAL),
    Column("status", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index("idx_orders_position", orders.c.grid_position_id)
Index("idx_orders_pending", orders.c.strategy_key, orders.c.status)

# ---- commands: 控制面输入队列 + 审计日志 ----
# 只由 CommandStore 写; strategy_key 为空表示全局命令（仅 kill_switch）。

commands = Table(
    "commands",
    metadata,
    Column("id", Text, primary_key=True),
    Column("strategy_key", Text),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("params", Text, nullable=False, server_default=text("'{}'")),
    Column("result", Text),
    Column("requested_by", Text, nullable=False, server_default=text("'web'")),
    Column("idempotency_key", Text, unique=True),
    Column("requested_at", Text, nullable=False),
    Column("started_at", Text),
    Column("finished_at", Text),
    Column("updated_at", Text, nullable=False),
)
Index("idx_commands_status", commands.c.status, commands.c.requested_at)
Index("idx_commands_strategy", commands.c.strategy_key, commands.c.requested_at)

# ---- strategy_configs: P3 策略实例参数 ----
# payload 是 StrategyConfigPayload 的 JSON 文本; 存储层不 import 策略层。
# 无删除语义: enabled=0 即停用, 保留行以维持策略键与历史账本的关联。

strategy_configs = Table(
    "strategy_configs",
    metadata,
    Column("strategy_key", Text, primary_key=True),
    Column("payload", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    Column("updated_by", Text, nullable=False, server_default=text("'web'")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)