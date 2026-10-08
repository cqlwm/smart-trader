"""存储层记录模型（Pydantic）

持久化层与外部（execution / strategy / 测试）之间的数据契约:

- `GridPositionRow`: `grid_positions` 表的一行, `GridPosition`（逻辑仓位）的持久化形态;
- `OrderRecord`: `orders` 表的一行: 一次真实委托的流水记录（审计/统计）;
- `IdRecord`: `order_ids` 表的一行: 预生成的订单号 + 确认状态 + 意图快照;
- `OrderIntent`: 下单意图快照: 崩溃后对账重建仓位与流水所需的最小上下文。

表结构定义见 `schema.py`; 语句与事务见 `order_journal.py`。
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict

from smart_trader.model import OrderSide, OrderStatus, OrderType, Symbol


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
