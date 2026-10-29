"""人工仓位声明 API 服务: 创建 / 撤销 / 列表

声明只影响告警抑制, 不改变账本与交易:
- 创建前必须能从心跳拿到该策略的新鲜对账数据, 标记数量不得超过当前差额;
- 声明按策略唯一, 新建即替换（旧声明软撤销, 保留审计）;
- 撤销幂等, 不物理删除。
"""

from datetime import UTC, datetime

from smart_trader.api import service
from smart_trader.api.schemas import PositionAckPage, PositionAckView
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.runtime import RuntimeSnapshot
from smart_trader.storage.position_ack_store import PositionAckStore
from smart_trader.storage.records import PositionAckRecord

_QTY_EPS = 1e-6


class AckTargetUnavailableError(SmartTraderError):
    """目标策略不在线 / 无对账数据 / 当前没有差额"""

    def __init__(self, strategy_key: str, reason: str):
        super().__init__(
            ErrorCode.POSITION_ACK_TARGET_UNAVAILABLE,
            f"无法为 {strategy_key} 创建人工仓位声明: {reason}",
        )
        self.strategy_key = strategy_key


class AckInvalidError(SmartTraderError):
    """标记数量超出当前差额"""

    def __init__(self, message: str):
        super().__init__(ErrorCode.POSITION_ACK_INVALID, message)


def _view(record: PositionAckRecord) -> PositionAckView:
    view = service.ack_view(record)
    assert view is not None
    return view


def create_ack(
    store: PositionAckStore,
    runtime: RuntimeSnapshot | None,
    strategy_key: str,
    *,
    external_qty: float,
    note: str = "",
) -> PositionAckView:
    target = next(
        (
            item
            for item in (runtime.strategies if runtime else [])
            if item.strategy_key == strategy_key
        ),
        None,
    )
    if target is None:
        raise AckTargetUnavailableError(strategy_key, "策略不在运行态心跳中")
    alive, _ = service.runtime_state(target, datetime.now(UTC))
    if not alive:
        raise AckTargetUnavailableError(strategy_key, "策略心跳已过期")

    snapshot = target.reconcile
    if (
        snapshot is None
        or snapshot.exchange_qty is None
        or snapshot.local_qty is None
    ):
        raise AckTargetUnavailableError(strategy_key, "尚无成功的持仓对账数据")
    surplus = snapshot.exchange_qty - snapshot.local_qty
    if surplus <= _QTY_EPS:
        raise AckTargetUnavailableError(
            strategy_key, "当前没有交易所多于本地的差额"
        )
    if external_qty > surplus + _QTY_EPS:
        raise AckInvalidError(
            f"标记数量 {external_qty:g} 大于当前差额 {surplus:g}"
        )

    record = store.create(
        strategy_key=strategy_key,
        symbol=target.symbol,
        position_side=target.position_side,
        external_qty=external_qty,
        note=note,
    )
    return _view(record)


def revoke_ack(store: PositionAckStore, ack_id: str) -> PositionAckView:
    return _view(store.revoke(ack_id))


def list_ack_page(
    store: PositionAckStore, *, limit: int = 50, offset: int = 0
) -> PositionAckPage:
    records = store.list(limit=limit, offset=offset)
    return PositionAckPage(
        items=[_view(record) for record in records],
        total=store.count(),
        limit=limit,
        offset=offset,
    )
