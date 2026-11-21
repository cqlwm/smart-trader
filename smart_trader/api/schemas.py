"""只读 API 响应契约

全部是派生视图, 不落库; 与 `storage/records.py` 解耦, 避免存储结构直接泄漏到 HTTP。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from smart_trader.model import (
    CommandAction,
    CommandStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionAckStatus,
    PositionCloseReason,
    PositionSide,
)
from smart_trader.strategy.config_payload import StrategyConfigPayload

PositionPhase = Literal["opening", "holding", "closing", "closed"]
OrderRole = Literal["entry", "exit"]
AlertSeverity = Literal["critical", "warning", "info"]
MarketDirection = Literal["long", "short", "neutral"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderView(ApiModel):
    order_id: str
    grid_position_id: str
    strategy_key: str
    symbol: str
    side: OrderSide
    type: OrderType
    role: OrderRole | None
    price: float | None
    quantity: float
    filled_qty: float
    avg_price: float | None
    status: OrderStatus | None
    unconfirmed: bool
    created_at: str
    updated_at: str


class PositionView(ApiModel):
    id: str
    strategy_key: str
    symbol: str
    position_side: PositionSide
    main_order_side: OrderSide
    phase: PositionPhase
    active: bool
    closed_reason: PositionCloseReason | None

    quantity: float
    price: float
    entry_filled: float
    entry_pending: float
    exit_filled: float
    realized_pnl: float

    opening_quantity: float
    opening_price: float
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    enable_stop_loss: bool
    stop_loss_rate: float
    enable_trailing_stop: bool
    trailing_stop_rate: float
    trailing_stop_activation_rate: float
    trailing_stop_price: float | None
    stop_loss_price: float | None
    take_profit_price: float | None

    pending_exit: OrderView | None
    order_count: int
    open_order_count: int
    created_at: str
    updated_at: str


class PositionDetailView(PositionView):
    orders: list[OrderView]


class PositionPage(ApiModel):
    items: list[PositionView]
    total: int
    limit: int
    offset: int


class ReconcileView(ApiModel):
    """持仓对账与出场跟踪运行态（心跳文件派生, 不落库）"""

    checked_at: str | None
    exchange_qty: float | None
    local_qty: float | None
    gap: float
    tracked_exit_orders: int
    max_exit_query_failures: int
    failing_exit_orders: list[str]


class PositionAckView(ApiModel):
    """人工外部仓位声明（API 写, 不参与交易）"""

    id: str
    strategy_key: str
    symbol: str
    position_side: PositionSide
    external_qty: float
    note: str
    status: PositionAckStatus
    acknowledged_by: str
    acknowledged_at: str
    revoked_at: str | None
    revoked_by: str | None


class PositionAckCreateRequest(ApiModel):
    external_qty: float = Field(gt=0)
    note: str = ""


class PositionAckPage(ApiModel):
    items: list[PositionAckView]
    total: int
    limit: int
    offset: int


class StrategySummaryView(ApiModel):
    strategy_key: str
    symbol: str
    position_side: PositionSide
    timeframe: str | None = None
    active_positions: int
    archived_positions: int
    realized_pnl: float
    open_orders: int
    unconfirmed_orders: int
    last_updated_at: str | None
    alive: bool = False
    running: bool | None = None
    heartbeat_at: str | None = None
    reconcile: ReconcileView | None = None
    ack: PositionAckView | None = None


class AlertView(ApiModel):
    severity: AlertSeverity
    code: str
    message: str
    strategy_key: str | None
    order_id: str | None
    created_at: str | None


class OverviewView(ApiModel):
    generated_at: str
    strategy_count: int
    active_position_count: int
    archived_position_count: int
    open_order_count: int
    unconfirmed_order_count: int
    realized_pnl_total: float
    realized_pnl_active: float
    strategies: list[StrategySummaryView]
    alerts: list[AlertView]


class OrderPage(ApiModel):
    items: list[OrderView]
    total: int
    limit: int
    offset: int


class HealthView(ApiModel):
    status: Literal["ok"]
    database: str
    server_time: str


class CommandRequest(ApiModel):
    """策略级命令请求"""

    action: CommandAction
    idempotency_key: str | None = None


class KillSwitchRequest(ApiModel):
    idempotency_key: str | None = None


class CommandView(ApiModel):
    id: str
    strategy_key: str | None
    action: CommandAction
    status: CommandStatus
    result: str | None
    requested_by: str
    requested_at: str
    started_at: str | None
    finished_at: str | None


class CommandPage(ApiModel):
    items: list[CommandView]
    total: int
    limit: int
    offset: int


class CandleView(ApiModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    finished: bool


class IndicatorPoint(ApiModel):
    timestamp: int
    alpha_value: float | None
    trend: int
    is_breakout: bool


class MarketChartView(ApiModel):
    symbol: str
    timeframe: str
    fetched_at: str
    candles: list[CandleView]
    indicator: list[IndicatorPoint]
    direction: MarketDirection
    trend: int
    last_price: float
    last_breakout_timestamp: int | None


class StrategyConfigView(ApiModel):
    """一条策略配置: 键 + 可序列化载荷 + 版本元数据"""

    strategy_key: str
    payload: StrategyConfigPayload
    version: int
    enabled: bool
    updated_by: str
    created_at: str
    updated_at: str


class StrategyConfigCreateRequest(ApiModel):
    strategy_key: str
    enabled: bool = True
    payload: StrategyConfigPayload


class StrategyConfigUpdateRequest(ApiModel):
    enabled: bool
    expected_version: int = Field(ge=1)
    payload: StrategyConfigPayload
