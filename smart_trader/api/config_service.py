"""策略配置 API 服务: 视图转换 / 创建 / 更新 / 活跃仓位护栏

护栏是 API 层的策略而不是仓储职责: 仓储只关心行与版本,
"有活跃仓位时不许禁用或改身份字段"需要账本视角, 因此在这里用只读 reader 判定。
"""

from smart_trader.api.schemas import StrategyConfigView
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.storage.config_store import ConfigNotFoundError, ConfigStore
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_payload import StrategyConfigPayload

# 活跃仓位存在时不可变字段: 它们决定策略的身份与账本归属方向
_LOCKED_FIELDS = ("symbol", "timeframe", "position_side", "master_side")


class ConfigInUseError(SmartTraderError):
    """活跃仓位存在时试图禁用配置或修改身份字段"""

    def __init__(self, strategy_key: str, changes: list[str]):
        super().__init__(
            ErrorCode.CONFIG_IN_USE,
            f"策略 {strategy_key} 存在活跃仓位, 不能修改: {', '.join(changes)};"
            " 请先平仓并重启策略进程",
        )
        self.strategy_key = strategy_key
        self.changes = changes


def config_view(record: StrategyConfigRecord) -> StrategyConfigView:
    """记录 → 视图; payload JSON 在 API 边界解析校验"""
    return StrategyConfigView(
        strategy_key=record.strategy_key,
        payload=StrategyConfigPayload.from_json(record.payload),
        version=record.version,
        enabled=record.enabled,
        updated_by=record.updated_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def list_config_views(store: ConfigStore) -> list[StrategyConfigView]:
    return [config_view(record) for record in store.list()]


def get_config_view(
    store: ConfigStore, strategy_key: str
) -> StrategyConfigView | None:
    record = store.get(strategy_key)
    if record is None:
        return None
    return config_view(record)


def create_config(
    store: ConfigStore,
    *,
    strategy_key: str,
    enabled: bool,
    payload: StrategyConfigPayload,
) -> StrategyConfigView:
    record = store.create(
        strategy_key=strategy_key,
        payload=payload.to_json(),
        enabled=enabled,
    )
    return config_view(record)


def update_config(
    store: ConfigStore,
    reader: OrderJournalReader,
    strategy_key: str,
    *,
    enabled: bool,
    expected_version: int,
    payload: StrategyConfigPayload,
) -> StrategyConfigView:
    """覆盖更新; 存在活跃仓位时拒绝禁用与身份字段变更（409）"""
    existing = store.get(strategy_key)
    if existing is None:
        raise ConfigNotFoundError(strategy_key)
    current = StrategyConfigPayload.from_json(existing.payload)

    active_positions = reader.load_positions(strategy_key=strategy_key)
    if active_positions:
        changes: list[str] = []
        if existing.enabled and not enabled:
            changes.append("enabled")
        old_identity = (
            current.symbol,
            current.timeframe,
            current.position_side,
            current.master_side,
        )
        new_identity = (
            payload.symbol,
            payload.timeframe,
            payload.position_side,
            payload.master_side,
        )
        changes.extend(
            name
            for name, old_value, new_value in zip(
                _LOCKED_FIELDS, old_identity, new_identity, strict=True
            )
            if old_value != new_value
        )
        if changes:
            raise ConfigInUseError(strategy_key, changes)

    record = store.update(
        strategy_key,
        payload=payload.to_json(),
        enabled=enabled,
        expected_version=expected_version,
    )
    return config_view(record)
