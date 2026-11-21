"""只读 API 服务层: 账本读取 + 派生视图 + 聚合

派生量一律复用 `GridPosition` 的计算属性, 不在这里重写数量/均价/盈亏公式。
"""

from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from typing import cast

from smart_trader.api.schemas import (
    AlertView,
    OrderPage,
    OrderRole,
    OrderView,
    OverviewView,
    PositionAckView,
    PositionDetailView,
    PositionPage,
    PositionPhase,
    PositionView,
    ReconcileView,
    StrategySummaryView,
)
from smart_trader.model import (
    OrderSide,
    OrderStatus,
    PositionAckStatus,
    PositionSide,
    ReconcileSnapshot,
    StrategyRuntime,
)
from smart_trader.runtime import RuntimeSnapshot
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.storage.records import (
    GridPositionRow,
    OrderRecord,
    PositionAckRecord,
)
from smart_trader.strategy.grid_position import GridPosition

_UNCONFIRMED_ALERT_LIMIT = 50
_RUNTIME_STALE_SECONDS = 15.0
# 出场单连续查询失败达到该次数必须告警（心跳每 2s, 完成 K 线每周期一次查询）
_EXIT_QUERY_ALERT_THRESHOLD = 3
_QTY_EPS = 1e-6


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _positions_with_orders(
    reader: OrderJournalReader, rows: list[GridPositionRow]
) -> list[GridPosition]:
    """仓位行 + 关联订单（一次查询）→ 逻辑仓位"""
    orders = reader.load_orders_for_positions([row.id for row in rows])
    by_position: dict[str, list[OrderRecord]] = {}
    for order in orders:
        by_position.setdefault(order.grid_position_id, []).append(order)
    return [
        GridPosition.from_row(row, by_position.get(row.id, [])) for row in rows
    ]


def _load_positions(
    reader: OrderJournalReader,
    *,
    strategy_key: str | None = None,
    include_closed: bool = False,
) -> list[GridPosition]:
    """仓位行 + 关联订单 → 逻辑仓位（一次查询取全部订单）"""
    return _positions_with_orders(
        reader,
        reader.load_positions(
            strategy_key=strategy_key, include_closed=include_closed
        ),
    )


def _order_role(
    order: OrderRecord, position_side: PositionSide | None
) -> OrderRole | None:
    """入场/出场角色: 按所属仓位的 position_side 归类"""
    if position_side is None:
        return None
    entry_side = (
        OrderSide.BUY if position_side == PositionSide.LONG else OrderSide.SELL
    )
    return "entry" if order.side == entry_side else "exit"


def _order_view(
    order: OrderRecord, position_side: PositionSide | None
) -> OrderView:
    return OrderView(
        order_id=order.order_id,
        grid_position_id=order.grid_position_id,
        strategy_key=order.strategy_key,
        symbol=order.symbol,
        side=order.side,
        type=order.type,
        role=_order_role(order, position_side),
        price=order.price,
        quantity=order.quantity,
        filled_qty=order.filled_qty,
        avg_price=order.avg_price,
        status=order.status,
        unconfirmed=order.status is None,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _stop_loss_price(position: GridPosition) -> float | None:
    """止损线: 跟踪止损读 ratchet 快照, 固定止损按均价实时计算"""
    if not position.enable_stop_loss:
        return None
    if position.enable_trailing_stop:
        return position.trailing_stop_price
    if position.price <= 0:
        return None
    if position.position_side == PositionSide.LONG:
        return position.price * (1 - position.stop_loss_rate)
    return position.price * (1 + position.stop_loss_rate)


def _take_profit_price(position: GridPosition) -> float | None:
    """固定止盈档位线: 仅在策略启用固定比例止盈时由调用方取用"""
    if position.price <= 0:
        return None
    if position.position_side == PositionSide.LONG:
        return position.price * (1 + position.fixed_take_profit_rate)
    return position.price * (1 - position.fixed_take_profit_rate)


def _entry_pending(position: GridPosition) -> float:
    """未了结入场单的剩余委托量"""
    return sum(
        max(0.0, order.quantity - order.filled_qty)
        for order in position.pending_entry_orders
    )


def _position_view(
    position: GridPosition, *, fixed_take_profit_enabled: bool
) -> PositionView:
    pending_exit = position.pending_exit_order
    return PositionView(
        id=position.id,
        strategy_key=position.strategy_key,
        symbol=position.symbol,
        position_side=position.position_side,
        main_order_side=position.main_order_side,
        phase=cast(PositionPhase, position.phase),
        active=position.active,
        closed_reason=position.closed_reason,
        quantity=position.quantity,
        price=position.price,
        entry_filled=position.entry_filled,
        entry_pending=_entry_pending(position),
        exit_filled=position.exit_filled,
        realized_pnl=position.realized_pnl,
        opening_quantity=position.opening_quantity,
        opening_price=position.opening_price,
        fixed_take_profit_rate=position.fixed_take_profit_rate,
        signal_min_take_profit_rate=position.signal_min_take_profit_rate,
        enable_stop_loss=position.enable_stop_loss,
        stop_loss_rate=position.stop_loss_rate,
        enable_trailing_stop=position.enable_trailing_stop,
        trailing_stop_rate=position.trailing_stop_rate,
        trailing_stop_activation_rate=position.trailing_stop_activation_rate,
        trailing_stop_price=position.trailing_stop_price,
        stop_loss_price=_stop_loss_price(position),
        take_profit_price=(
            _take_profit_price(position) if fixed_take_profit_enabled else None
        ),
        pending_exit=(
            _order_view(pending_exit, position.position_side)
            if pending_exit is not None
            else None
        ),
        order_count=len(position.orders),
        open_order_count=sum(
            1
            for order in position.orders
            if order.status is None or order.status.is_open()
        ),
        created_at=position.created_at,
        updated_at=position.updated_at,
    )


def list_position_views(
    reader: OrderJournalReader,
    *,
    strategy_key: str | None = None,
    include_closed: bool = False,
    fixed_take_profit_keys: AbstractSet[str] = frozenset(),
) -> list[PositionView]:
    """仓位列表（默认只含活跃仓位）

    @param fixed_take_profit_keys 启用固定比例止盈的策略键; 未命中的策略
        不返回止盈线（费率随仓位持久化, 不代表开关已启用）
    """
    positions = _load_positions(
        reader, strategy_key=strategy_key, include_closed=include_closed
    )
    return [
        _position_view(
            position,
            fixed_take_profit_enabled=(
                position.strategy_key in fixed_take_profit_keys
            ),
        )
        for position in positions
    ]


def list_archived_position_views(
    reader: OrderJournalReader,
    *,
    strategy_key: str | None = None,
    limit: int = 20,
    offset: int = 0,
    fixed_take_profit_keys: AbstractSet[str] = frozenset(),
) -> PositionPage:
    """归档仓位分页（更新时间倒序）; 订单只为当前页仓位加载"""
    rows = reader.load_archived_positions(
        strategy_key=strategy_key, limit=limit, offset=offset
    )
    positions = _positions_with_orders(reader, rows)
    return PositionPage(
        items=[
            _position_view(
                position,
                fixed_take_profit_enabled=(
                    position.strategy_key in fixed_take_profit_keys
                ),
            )
            for position in positions
        ],
        total=reader.count_archived_positions(strategy_key=strategy_key),
        limit=limit,
        offset=offset,
    )


def get_position_detail(
    reader: OrderJournalReader,
    position_id: str,
    *,
    fixed_take_profit_keys: AbstractSet[str] = frozenset(),
) -> PositionDetailView | None:
    """仓位详情 + 全部订单; 不存在返回 None"""
    rows = reader.load_positions_by_ids([position_id])
    if not rows:
        return None
    orders = reader.load_orders_for_positions([position_id])
    position = GridPosition.from_row(rows[0], orders)
    base = _position_view(
        position,
        fixed_take_profit_enabled=position.strategy_key in fixed_take_profit_keys,
    )
    return PositionDetailView(
        **base.model_dump(),
        orders=[
            _order_view(order, position.position_side) for order in orders
        ],
    )


def list_order_views(
    reader: OrderJournalReader,
    *,
    strategy_key: str | None = None,
    status: OrderStatus | None = None,
    unconfirmed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> OrderPage:
    """订单分页 + 归属仓位方向"""
    records = reader.query_orders(
        strategy_key=strategy_key,
        status=status,
        unconfirmed=unconfirmed,
        limit=limit,
        offset=offset,
    )
    total = reader.count_orders(
        strategy_key=strategy_key, status=status, unconfirmed=unconfirmed
    )
    rows = reader.load_positions_by_ids(
        sorted({record.grid_position_id for record in records})
    )
    side_by_id: dict[str, PositionSide] = {
        row.id: row.position_side for row in rows
    }
    items = [
        _order_view(record, side_by_id.get(record.grid_position_id))
        for record in records
    ]
    return OrderPage(items=items, total=total, limit=limit, offset=offset)


def runtime_state(
    runtime: StrategyRuntime, now: datetime
) -> tuple[bool, bool | None]:
    """心跳是否新鲜; 新鲜才回报 is_running, 过期视为离线"""
    if not runtime.heartbeat_at:
        return False, None
    try:
        heartbeat = datetime.fromisoformat(runtime.heartbeat_at)
    except ValueError:
        return False, None
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    alive = (now - heartbeat).total_seconds() <= _RUNTIME_STALE_SECONDS
    return (True, runtime.is_running) if alive else (False, None)


def _reconcile_view(snapshot: ReconcileSnapshot | None) -> ReconcileView | None:
    """运行态对账快照 → API 视图; 缺失保持 None"""
    if snapshot is None:
        return None
    return ReconcileView(**snapshot.model_dump())


def ack_view(record: PositionAckRecord | None) -> PositionAckView | None:
    """人工仓位声明 → API 视图; 缺失保持 None"""
    if record is None:
        return None
    return PositionAckView(
        id=record.id,
        strategy_key=record.strategy_key,
        symbol=record.symbol,
        position_side=record.position_side,
        external_qty=record.external_qty,
        note=record.note,
        status=record.status,
        acknowledged_by=record.acknowledged_by,
        acknowledged_at=record.acknowledged_at,
        revoked_at=record.revoked_at,
        revoked_by=record.revoked_by,
    )


def _summaries_from(
    positions: list[GridPosition],
    runtime: RuntimeSnapshot | None = None,
    acks: list[PositionAckRecord] | None = None,
) -> list[StrategySummaryView]:
    grouped: dict[str, list[GridPosition]] = {}
    for position in positions:
        grouped.setdefault(position.strategy_key, []).append(position)

    now = datetime.now(UTC)
    runtime_by_key = (
        {item.strategy_key: item for item in runtime.strategies} if runtime else {}
    )
    ack_by_key = {
        ack.strategy_key: ack
        for ack in (acks or [])
        if ack.status is PositionAckStatus.ACTIVE
    }

    summaries: list[StrategySummaryView] = []
    for strategy_key in sorted(grouped):
        group = grouped[strategy_key]
        orders = [order for position in group for order in position.orders]
        timestamps = [position.updated_at for position in group] + [
            order.updated_at for order in orders
        ]
        runtime_info = runtime_by_key.pop(strategy_key, None)
        alive, running = (
            runtime_state(runtime_info, now)
            if runtime_info is not None
            else (False, None)
        )
        summaries.append(
            StrategySummaryView(
                strategy_key=strategy_key,
                symbol=(
                    runtime_info.symbol
                    if runtime_info is not None and alive
                    else group[0].symbol
                ),
                position_side=(
                    runtime_info.position_side
                    if runtime_info is not None and alive
                    else group[0].position_side
                ),
                timeframe=(
                    runtime_info.timeframe
                    if runtime_info is not None and alive
                    else None
                ),
                active_positions=sum(1 for position in group if position.active),
                archived_positions=sum(
                    1 for position in group if not position.active
                ),
                realized_pnl=sum(position.realized_pnl for position in group),
                open_orders=sum(
                    1
                    for order in orders
                    if order.status is not None and order.status.is_open()
                ),
                unconfirmed_orders=sum(
                    1 for order in orders if order.status is None
                ),
                last_updated_at=max(
                    (timestamp for timestamp in timestamps if timestamp),
                    default=None,
                ),
                alive=alive,
                running=running,
                heartbeat_at=(
                    runtime_info.heartbeat_at
                    if runtime_info is not None and alive
                    else None
                ),
                reconcile=(
                    _reconcile_view(runtime_info.reconcile)
                    if runtime_info is not None and alive
                    else None
                ),
                ack=ack_view(ack_by_key.get(strategy_key)),
            )
        )

    # 运行中但尚未写账本的策略: 用运行态补齐, 否则前端看不到它
    for strategy_key in sorted(runtime_by_key):
        runtime_info = runtime_by_key[strategy_key]
        alive, running = runtime_state(runtime_info, now)
        summaries.append(
            StrategySummaryView(
                strategy_key=strategy_key,
                symbol=runtime_info.symbol,
                position_side=runtime_info.position_side,
                timeframe=runtime_info.timeframe,
                active_positions=0,
                archived_positions=0,
                realized_pnl=0.0,
                open_orders=0,
                unconfirmed_orders=0,
                last_updated_at=runtime_info.heartbeat_at or None,
                alive=alive,
                running=running,
                heartbeat_at=runtime_info.heartbeat_at or None,
                reconcile=(
                    _reconcile_view(runtime_info.reconcile) if alive else None
                ),
                ack=ack_view(ack_by_key.get(strategy_key)),
            )
        )
    return summaries


def list_strategy_summaries(
    reader: OrderJournalReader,
    runtime: RuntimeSnapshot | None = None,
    acks: list[PositionAckRecord] | None = None,
) -> list[StrategySummaryView]:
    """按 strategy_key 聚合的策略摘要（账本 + 运行态心跳 + 人工仓位声明）"""
    return _summaries_from(
        _load_positions(reader, include_closed=True), runtime, acks
    )


def _alert(order: OrderRecord) -> AlertView:
    side = "buy" if order.side == OrderSide.BUY else "sell"
    return AlertView(
        severity="critical",
        code="unconfirmed_order",
        message=(
            f"订单结果未确认（WAL）: {order.symbol} {side} "
            f"qty={order.quantity:g} order_id={order.order_id}"
        ),
        strategy_key=order.strategy_key,
        order_id=order.order_id,
        created_at=order.created_at,
    )


def _qty_text(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


def _surplus_alert(
    item: StrategyRuntime,
    snapshot: ReconcileSnapshot,
    ack: PositionAckRecord | None,
) -> AlertView:
    surplus = (snapshot.exchange_qty or 0.0) - (snapshot.local_qty or 0.0)
    if ack is None:
        message = (
            f"交易所持仓多于本地 {surplus:g}: "
            f"exchange={_qty_text(snapshot.exchange_qty)} "
            f"local={_qty_text(snapshot.local_qty)}; "
            "来源不明, 需人工确认处置"
        )
    else:
        unexplained = surplus - ack.external_qty
        message = (
            f"未解释差额 {unexplained:g}（交易所多于本地 {surplus:g}, "
            f"已标记人工 {ack.external_qty:g}）; 请补充标记或处置"
        )
    return AlertView(
        severity="critical",
        code="exchange_position_surplus",
        message=message,
        strategy_key=item.strategy_key,
        order_id=None,
        created_at=snapshot.checked_at,
    )


def _ack_stale_alert(
    item: StrategyRuntime,
    snapshot: ReconcileSnapshot,
    ack: PositionAckRecord,
    reason: str,
) -> AlertView:
    surplus = (snapshot.exchange_qty or 0.0) - (snapshot.local_qty or 0.0)
    return AlertView(
        severity="warning",
        code="position_ack_stale",
        message=(
            f"人工仓位标记 {ack.external_qty:g} 已失效: {reason} "
            f"(当前差额 {surplus:g}); 请更新或撤销标记"
        ),
        strategy_key=item.strategy_key,
        order_id=ack.id,
        created_at=snapshot.checked_at,
    )


def _runtime_alerts(
    runtime: RuntimeSnapshot | None,
    acks: list[PositionAckRecord] | None,
    now: datetime,
) -> list[AlertView]:
    """对账差异、人工仓位声明与出场单查询失败告警（只读, 不触发交易动作）

    - 交易所多于本地且未标记: critical `exchange_position_surplus`;
    - 已标记但仍有未解释差额: critical, 消息给出未解释量;
    - 标记数量大于当前差额 / 差额已消失: warning `position_ack_stale`;
    - 单个出场单连续查询失败达到阈值: warning `exit_query_failing`。
    """
    if runtime is None:
        return []
    ack_by_key = {
        ack.strategy_key: ack
        for ack in (acks or [])
        if ack.status is PositionAckStatus.ACTIVE
    }
    alerts: list[AlertView] = []
    for item in runtime.strategies:
        alive, _ = runtime_state(item, now)
        snapshot = item.reconcile
        if not alive or snapshot is None:
            continue
        ack = ack_by_key.get(item.strategy_key)
        if snapshot.exchange_qty is not None and snapshot.local_qty is not None:
            surplus = snapshot.exchange_qty - snapshot.local_qty
            if surplus > _QTY_EPS:
                if ack is None:
                    alerts.append(_surplus_alert(item, snapshot, None))
                elif ack.external_qty > surplus + _QTY_EPS:
                    alerts.append(
                        _ack_stale_alert(
                            item,
                            snapshot,
                            ack,
                            "标记数量大于当前交易所多于本地的差额",
                        )
                    )
                elif surplus - ack.external_qty > _QTY_EPS:
                    alerts.append(_surplus_alert(item, snapshot, ack))
                # 标记覆盖全部差额: 静默
            elif ack is not None:
                alerts.append(
                    _ack_stale_alert(
                        item,
                        snapshot,
                        ack,
                        "当前交易所已无多于本地的差额",
                    )
                )
        if (
            snapshot.max_exit_query_failures
            >= _EXIT_QUERY_ALERT_THRESHOLD
        ):
            failing = ",".join(snapshot.failing_exit_orders) or "?"
            alerts.append(
                AlertView(
                    severity="warning",
                    code="exit_query_failing",
                    message=(
                        f"出场单连续查询失败 "
                        f"{snapshot.max_exit_query_failures} 次: {failing}; "
                        "策略仍保留待跟踪, 请检查交易所连通性"
                    ),
                    strategy_key=item.strategy_key,
                    order_id=(
                        snapshot.failing_exit_orders[0]
                        if snapshot.failing_exit_orders
                        else None
                    ),
                    created_at=item.heartbeat_at or snapshot.checked_at,
                )
            )
    return alerts


def build_overview(
    reader: OrderJournalReader,
    runtime: RuntimeSnapshot | None = None,
    acks: list[PositionAckRecord] | None = None,
) -> OverviewView:
    """全库聚合: 计数 / 盈亏 / 策略摘要 / 对账与未确认订单告警"""
    positions = _load_positions(reader, include_closed=True)
    orders = [order for position in positions for order in position.orders]
    unconfirmed = [order for order in orders if order.status is None]
    summaries = _summaries_from(positions, runtime, acks)

    return OverviewView(
        generated_at=_now(),
        strategy_count=len(summaries),
        active_position_count=sum(1 for position in positions if position.active),
        archived_position_count=sum(
            1 for position in positions if not position.active
        ),
        open_order_count=sum(
            1
            for order in orders
            if order.status is not None and order.status.is_open()
        ),
        unconfirmed_order_count=len(unconfirmed),
        realized_pnl_total=sum(position.realized_pnl for position in positions),
        realized_pnl_active=sum(
            position.realized_pnl for position in positions if position.active
        ),
        strategies=summaries,
        alerts=[
            *_runtime_alerts(runtime, acks, datetime.now(UTC)),
            *[_alert(order) for order in unconfirmed[:_UNCONFIRMED_ALERT_LIMIT]],
        ],
    )

