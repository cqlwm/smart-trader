"""存储层记录模型（Pydantic）

持久化层与外部（execution / strategy / 测试）之间的数据契约:

- `GridPositionRow`: `grid_positions` 表的一行, `GridPosition`（逻辑仓位）的持久化形态;
  只含策略参数与运行时状态, 价格 / 数量 / 相位由 `orders` 派生;
- `OrderRecord`: `orders` 表的一行: 一次委托（`status` 为空 = 结果未确认）。

表结构定义见 `schema.py`; 语句与事务见 `order_journal.py`。
"""

from pydantic import BaseModel, ConfigDict

from smart_trader.model import (
    CommandAction,
    CommandStatus,
    DisablePositionPolicy,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionAckStatus,
    PositionCloseReason,
    PositionSide,
    StrategyLifecycle,
)


class GridPositionRow(BaseModel):
    """grid_positions 表的一行, `GridPosition`（逻辑仓位）的持久化形态"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    strategy_key: str
    symbol: str
    position_side: PositionSide
    main_order_side: OrderSide
    opening_quantity: float = 0.0
    opening_price: float = 0.0
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    enable_stop_loss: bool = False
    stop_loss_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.0
    trailing_stop_activation_rate: float = 0.0
    trailing_stop_price: float | None = None
    active: bool = True
    closed_reason: PositionCloseReason | None = None
    created_at: str = ""
    updated_at: str = ""


class OrderRecord(BaseModel):
    """orders 表的一行: 一次委托; `status` 为空表示结果未确认（WAL 标记）"""

    model_config = ConfigDict(extra="forbid")

    order_id: str = ""
    grid_position_id: str
    strategy_key: str
    symbol: str
    side: OrderSide
    type: OrderType
    price: float | None = None
    quantity: float = 0.0
    filled_qty: float = 0.0
    avg_price: float | None = None
    status: OrderStatus | None = None
    created_at: str = ""
    updated_at: str = ""


class CommandRecord(BaseModel):
    """commands 表的一行: 控制命令 + 审计记录"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    strategy_key: str | None = None
    action: CommandAction
    status: CommandStatus = CommandStatus.PENDING
    params: str = "{}"
    result: str | None = None
    requested_by: str = "web"
    idempotency_key: str | None = None
    requested_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = ""


class StrategyConfigRecord(BaseModel):
    """strategy_configs 表的一行: 一个策略实例的参数与元数据

    `payload` 为 JSON 原样文本, 解析与校验由策略层 `StrategyConfigPayload` 负责,
    存储层不 import 策略层, 避免依赖倒置。
    """

    model_config = ConfigDict(extra="forbid")

    strategy_key: str = ""
    payload: str
    version: int = 1
    enabled: bool = True
    updated_by: str = "web"
    created_at: str = ""
    updated_at: str = ""
    # 生命周期: desired_running 是持久化暂停意图; lifecycle 是停用流程状态
    desired_running: bool = True
    lifecycle: StrategyLifecycle = StrategyLifecycle.ACTIVE
    disable_policy: DisablePositionPolicy | None = None
    disable_requested_at: str | None = None
    disable_completed_at: str | None = None
    disable_error: str | None = None


class PositionAckRecord(BaseModel):
    """position_acks 表的一行: 人工外部仓位声明（不参与交易）"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    strategy_key: str
    symbol: str
    position_side: PositionSide
    external_qty: float
    note: str = ""
    status: PositionAckStatus = PositionAckStatus.ACTIVE
    acknowledged_by: str = "web"
    acknowledged_at: str = ""
    revoked_at: str | None = None
    revoked_by: str | None = None
    updated_at: str = ""