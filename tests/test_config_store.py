"""ConfigStore: 创建 / 查询 / 版本乐观锁 / 缺表迁移提示"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_trader.exceptions import ErrorCode
from smart_trader.model import DisablePositionPolicy, StrategyLifecycle
from smart_trader.storage.config_store import (
    ConfigKeyExistsError,
    ConfigNotFoundError,
    ConfigStore,
    ConfigVersionConflictError,
)
from smart_trader.storage.order_journal import OrderJournal
from tests.test_storage_schema import create_pre_reason_database

KEY = "signal_grid_short_sell_dogeusdt_1m"
PAYLOAD = '{"symbol":"DOGE/USDT","max_order":50}'
UPDATED_PAYLOAD = '{"symbol":"DOGE/USDT","max_order":10}'


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def store(db_path: Path, journal: OrderJournal) -> Iterator[ConfigStore]:
    instance = ConfigStore(str(db_path))
    yield instance
    instance.close()


def test_create_persists_version_one_and_metadata(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)

    assert created.version == 1, "新建配置版本必须从 1 开始"
    assert created.enabled is True
    assert created.updated_by == "web"
    assert created.payload == PAYLOAD
    assert created.created_at and created.updated_at
    assert store.get(KEY) == created, "写入后回读必须一致"


def test_create_duplicate_key_raises_conflict(store: ConfigStore):
    store.create(strategy_key=KEY, payload=PAYLOAD)

    with pytest.raises(ConfigKeyExistsError) as raised:
        store.create(strategy_key=KEY, payload=PAYLOAD)

    assert raised.value.error_code is ErrorCode.CONFIG_KEY_EXISTS
    assert store.list() == [store.get(KEY)], "冲突不得产生第二行"


@pytest.mark.parametrize("raw", ["", "   "])
def test_create_rejects_blank_key(store: ConfigStore, raw: str):
    with pytest.raises(ValueError, match="strategy_key"):
        store.create(strategy_key=raw, payload=PAYLOAD)


def test_get_unknown_key_returns_none(store: ConfigStore):
    assert store.get("missing") is None


def test_list_is_sorted_by_strategy_key(store: ConfigStore):
    store.create(strategy_key="z_strategy", payload=PAYLOAD)
    store.create(strategy_key="a_strategy", payload=PAYLOAD)

    assert [record.strategy_key for record in store.list()] == [
        "a_strategy",
        "z_strategy",
    ], "列表必须按策略键稳定排序"


def test_update_replaces_payload_and_increments_version(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)

    updated = store.update(
        KEY,
        payload=UPDATED_PAYLOAD,
        expected_version=created.version,
        updated_by="ops",
    )

    assert updated.version == 2, "每次更新版本必须递增"
    assert updated.payload == UPDATED_PAYLOAD
    assert updated.enabled is True, "参数更新不得顺带改启停状态"
    assert updated.lifecycle is StrategyLifecycle.ACTIVE
    assert updated.updated_by == "ops"
    assert store.get(KEY) == updated


def test_update_with_stale_version_raises_conflict(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)
    store.update(
        KEY,
        payload=UPDATED_PAYLOAD,
        expected_version=created.version,
    )

    with pytest.raises(ConfigVersionConflictError) as raised:
        store.update(
            KEY,
            payload=PAYLOAD,
            expected_version=created.version,
        )

    assert raised.value.error_code is ErrorCode.CONFIG_VERSION_CONFLICT
    assert "期望 1" in str(raised.value) and "当前 2" in str(raised.value)
    current = store.get(KEY)
    assert current is not None and current.version == 2, "冲突不得改写当前行"


def test_update_unknown_key_raises_not_found(store: ConfigStore):
    with pytest.raises(ConfigNotFoundError) as raised:
        store.update(
            "missing",
            payload=PAYLOAD,
            expected_version=1,
        )

    assert raised.value.error_code is ErrorCode.CONFIG_NOT_FOUND


def test_missing_table_raises_migration_hint(tmp_path: Path):
    path = tmp_path / "legacy.db"
    create_pre_reason_database(str(path))

    with pytest.raises(ValueError, match="003_add_strategy_configs"):
        ConfigStore(str(path))


def test_request_disable_sets_lifecycle_and_policy(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)

    updated = store.request_disable(
        KEY,
        policy=DisablePositionPolicy.KEEP,
        expected_version=created.version,
    )

    assert updated.version == 2
    assert updated.enabled is False
    assert updated.desired_running is False
    assert updated.lifecycle is StrategyLifecycle.DISABLING
    assert updated.disable_policy is DisablePositionPolicy.KEEP
    assert updated.disable_requested_at is not None
    assert updated.disable_completed_at is None


def test_complete_disable_only_transitions_from_disabling(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)
    store.request_disable(
        KEY,
        policy=DisablePositionPolicy.KEEP,
        expected_version=created.version,
    )

    completed = store.complete_disable(KEY, StrategyLifecycle.GUARD)

    assert completed is not None
    assert completed.lifecycle is StrategyLifecycle.GUARD
    assert completed.disable_completed_at is not None
    assert completed.version == 3
    assert store.complete_disable(KEY, StrategyLifecycle.DISABLED) is None, (
        "非 disabling 状态不得被完成写入覆盖"
    )


def test_enable_resets_disable_state(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)
    store.request_disable(
        KEY,
        policy=DisablePositionPolicy.ABANDON,
        expected_version=created.version,
    )

    enabled = store.enable(KEY, expected_version=2)

    assert enabled.version == 3
    assert enabled.enabled is True
    assert enabled.desired_running is True
    assert enabled.lifecycle is StrategyLifecycle.ACTIVE
    assert enabled.disable_policy is None
    assert enabled.disable_requested_at is None
    assert enabled.disable_completed_at is None


def test_set_desired_running_does_not_bump_version(store: ConfigStore):
    created = store.create(strategy_key=KEY, payload=PAYLOAD)

    updated = store.set_desired_running(KEY, False)

    assert updated.desired_running is False
    assert updated.version == created.version, (
        "运行意图不属于参数版本, 不得递增 version"
    )
    assert updated.lifecycle is StrategyLifecycle.ACTIVE
