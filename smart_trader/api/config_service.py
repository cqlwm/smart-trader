"""策略配置 API 服务: 视图转换 / 创建 / 更新 / 启停 / 不可变字段护栏

护栏是 API 层的策略而不是仓储职责: 身份字段创建后不可变是配置语义。
停用不再要求"先平仓", 而是进入生命周期流程, 由策略运行时按所选
持仓处置策略执行（见 .memory/02-domain-model 十二节）。
"""

from smart_trader.api.schemas import StrategyConfigView
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import DisablePositionPolicy
from smart_trader.storage.config_store import ConfigNotFoundError, ConfigStore
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_payload import StrategyConfigPayload

# 创建后不可变的身份字段: 它们决定策略的账本归属、数据源与持仓方向语义
_IMMUTABLE_FIELDS = (
    "symbol",
    "timeframe",
    "data_source",
    "position_side",
    "master_side",
)


class ConfigFieldImmutableError(SmartTraderError):
    """试图修改创建后不可变的身份字段"""

    def __init__(self, strategy_key: str, changes: list[str]):
        super().__init__(
            ErrorCode.CONFIG_FIELD_IMMUTABLE,
            f"策略 {strategy_key} 的身份字段创建后不可修改: "
            f"{', '.join(changes)}; 请新建策略配置",
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
        desired_running=record.desired_running,
        lifecycle=record.lifecycle,
        disable_policy=record.disable_policy,
        disable_requested_at=record.disable_requested_at,
        disable_completed_at=record.disable_completed_at,
        disable_error=record.disable_error,
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
    strategy_key: str,
    *,
    expected_version: int,
    payload: StrategyConfigPayload,
) -> StrategyConfigView:
    """覆盖更新参数; 身份字段不可变, 启停不改 enabled（走 enable/disable）"""
    existing = store.get(strategy_key)
    if existing is None:
        raise ConfigNotFoundError(strategy_key)
    current = StrategyConfigPayload.from_json(existing.payload)

    immutable_changes = [
        name
        for name in _IMMUTABLE_FIELDS
        if getattr(current, name) != getattr(payload, name)
    ]
    if immutable_changes:
        raise ConfigFieldImmutableError(strategy_key, immutable_changes)

    record = store.update(
        strategy_key,
        payload=payload.to_json(),
        expected_version=expected_version,
    )
    return config_view(record)


def disable_config(
    store: ConfigStore,
    strategy_key: str,
    *,
    policy: DisablePositionPolicy,
    expected_version: int,
) -> StrategyConfigView:
    """请求停用: 立即暂停并进入流程, 运行时按 policy 处置持仓"""
    record = store.request_disable(
        strategy_key,
        policy=policy,
        expected_version=expected_version,
    )
    return config_view(record)


def enable_config(
    store: ConfigStore,
    strategy_key: str,
    *,
    expected_version: int,
) -> StrategyConfigView:
    """重新启用: 恢复 active 生命周期与运行意图"""
    record = store.enable(
        strategy_key,
        expected_version=expected_version,
    )
    return config_view(record)
