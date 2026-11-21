"""策略配置仓储（strategy_configs 表）

- 只写 `strategy_configs`, 不碰账本表 / 命令表;
- `version` 乐观锁: 更新必须携带 `expected_version`, 冲突抛
  `ConfigVersionConflictError`, 由 API 映射 409;
- 无删除接口: `enabled=False` 即停用, 保留行以维持策略键与历史账本的关联;
- 表不存在时响亮报错并指向迁移脚本, 不在业务代码里自动建表。
"""

from datetime import UTC, datetime

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import DisablePositionPolicy, StrategyLifecycle
from smart_trader.storage import schema
from smart_trader.storage.engine import create_engine_for_path
from smart_trader.storage.records import StrategyConfigRecord


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConfigStoreError(SmartTraderError):
    """策略配置仓储错误基类"""


class ConfigNotFoundError(ConfigStoreError):
    def __init__(self, strategy_key: str):
        super().__init__(
            ErrorCode.CONFIG_NOT_FOUND, f"策略配置不存在: {strategy_key}"
        )
        self.strategy_key = strategy_key


class ConfigVersionConflictError(ConfigStoreError):
    def __init__(self, strategy_key: str, expected: int, actual: int):
        super().__init__(
            ErrorCode.CONFIG_VERSION_CONFLICT,
            f"策略配置 {strategy_key} 版本冲突: 期望 {expected}, 当前 {actual}",
        )
        self.strategy_key = strategy_key
        self.expected = expected
        self.actual = actual


class ConfigKeyExistsError(ConfigStoreError):
    def __init__(self, strategy_key: str):
        super().__init__(
            ErrorCode.CONFIG_KEY_EXISTS, f"策略配置已存在: {strategy_key}"
        )
        self.strategy_key = strategy_key


class ConfigStore:
    """策略配置的读写仓储（策略进程与 API 进程共用）"""

    def __init__(self, db_path: str):
        self._engine = create_engine_for_path(db_path)
        with self._engine.connect() as conn:
            self._require_table(conn)

    @staticmethod
    def _require_table(conn: Connection) -> None:
        existing = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='strategy_configs'"
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ValueError(
                "strategy_configs 表不存在; 请执行 "
                "scripts/migrations/003_add_strategy_configs.py <db_path>"
            )

    def close(self) -> None:
        self._engine.dispose()

    # ---- 写 ----

    def create(
        self,
        *,
        strategy_key: str,
        payload: str,
        enabled: bool = True,
        updated_by: str = "web",
    ) -> StrategyConfigRecord:
        """新建配置（版本 1）; 主键冲突抛 ConfigKeyExistsError"""
        key = strategy_key.strip()
        if not key:
            raise ValueError("strategy_key 不能为空")
        now = _now()
        record = StrategyConfigRecord(
            strategy_key=key,
            payload=payload,
            version=1,
            enabled=enabled,
            updated_by=updated_by or "web",
            created_at=now,
            updated_at=now,
            desired_running=enabled,
            lifecycle=(
                StrategyLifecycle.ACTIVE
                if enabled
                else StrategyLifecycle.DISABLED
            ),
        )
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(schema.strategy_configs).values(
                        **record.model_dump(mode="json")
                    )
                )
        except IntegrityError as exc:
            raise ConfigKeyExistsError(key) from exc
        return record

    def update(
        self,
        strategy_key: str,
        *,
        payload: str,
        expected_version: int,
        updated_by: str = "web",
    ) -> StrategyConfigRecord:
        """覆盖参数: 版本必须匹配, 成功后版本 +1; 不改启停与生命周期"""
        key = strategy_key.strip()
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.strategy_configs)
                .where(
                    schema.strategy_configs.c.strategy_key == key,
                    schema.strategy_configs.c.version == expected_version,
                )
                .values(
                    payload=payload,
                    updated_by=updated_by or "web",
                    version=expected_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                current = conn.execute(
                    select(schema.strategy_configs.c.version).where(
                        schema.strategy_configs.c.strategy_key == key
                    )
                ).scalar_one_or_none()
                if current is None:
                    raise ConfigNotFoundError(key)
                raise ConfigVersionConflictError(
                    key, expected_version, int(current)
                )
        updated = self.get(key)
        if updated is None:
            raise ConfigNotFoundError(key)
        return updated

    def set_desired_running(
        self, strategy_key: str, running: bool
    ) -> StrategyConfigRecord:
        """持久化暂停意图; 不属于参数版本, 不递增 version"""
        key = strategy_key.strip()
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.strategy_configs)
                .where(schema.strategy_configs.c.strategy_key == key)
                .values(desired_running=running, updated_at=now)
            )
            if result.rowcount != 1:
                raise ConfigNotFoundError(key)
        updated = self.get(key)
        if updated is None:
            raise ConfigNotFoundError(key)
        return updated

    def request_disable(
        self,
        strategy_key: str,
        *,
        policy: DisablePositionPolicy,
        expected_version: int,
        updated_by: str = "web",
    ) -> StrategyConfigRecord:
        """请求停用: 立即置暂停意图与 disabling, 由运行时执行持仓处置"""
        key = strategy_key.strip()
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.strategy_configs)
                .where(
                    schema.strategy_configs.c.strategy_key == key,
                    schema.strategy_configs.c.version == expected_version,
                )
                .values(
                    enabled=False,
                    desired_running=False,
                    lifecycle=StrategyLifecycle.DISABLING.value,
                    disable_policy=policy.value,
                    disable_requested_at=now,
                    disable_completed_at=None,
                    disable_error=None,
                    version=expected_version + 1,
                    updated_by=updated_by or "web",
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                self._raise_version_error(conn, key, expected_version)
        updated = self.get(key)
        if updated is None:
            raise ConfigNotFoundError(key)
        return updated

    def enable(
        self,
        strategy_key: str,
        *,
        expected_version: int,
        updated_by: str = "web",
    ) -> StrategyConfigRecord:
        """启用: 恢复正常生命周期并清除停用流程状态"""
        key = strategy_key.strip()
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.strategy_configs)
                .where(
                    schema.strategy_configs.c.strategy_key == key,
                    schema.strategy_configs.c.version == expected_version,
                )
                .values(
                    enabled=True,
                    desired_running=True,
                    lifecycle=StrategyLifecycle.ACTIVE.value,
                    disable_policy=None,
                    disable_requested_at=None,
                    disable_completed_at=None,
                    disable_error=None,
                    version=expected_version + 1,
                    updated_by=updated_by or "web",
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                self._raise_version_error(conn, key, expected_version)
        updated = self.get(key)
        if updated is None:
            raise ConfigNotFoundError(key)
        return updated

    def complete_disable(
        self,
        strategy_key: str,
        lifecycle: StrategyLifecycle,
        *,
        error: str | None = None,
    ) -> StrategyConfigRecord | None:
        """运行时完成停用处置; 仅当仍处于 disabling 时生效（防止覆盖并发启用）"""
        if lifecycle not in (StrategyLifecycle.GUARD, StrategyLifecycle.DISABLED):
            raise ValueError(f"非法停用完成态: {lifecycle}")
        key = strategy_key.strip()
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.strategy_configs)
                .where(
                    schema.strategy_configs.c.strategy_key == key,
                    schema.strategy_configs.c.lifecycle
                    == StrategyLifecycle.DISABLING.value,
                )
                .values(
                    lifecycle=lifecycle.value,
                    disable_completed_at=now,
                    disable_error=error,
                    version=schema.strategy_configs.c.version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
        return self.get(key)

    @staticmethod
    def _raise_version_error(
        conn: Connection, strategy_key: str, expected_version: int
    ) -> None:
        current = conn.execute(
            select(schema.strategy_configs.c.version).where(
                schema.strategy_configs.c.strategy_key == strategy_key
            )
        ).scalar_one_or_none()
        if current is None:
            raise ConfigNotFoundError(strategy_key)
        raise ConfigVersionConflictError(
            strategy_key, expected_version, int(current)
        )

    # ---- 读 ----

    def get(self, strategy_key: str) -> StrategyConfigRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.strategy_configs).where(
                    schema.strategy_configs.c.strategy_key == strategy_key
                )
            ).mappings().first()
        if row is None:
            return None
        return StrategyConfigRecord.model_validate(dict(row))

    def list(self) -> list[StrategyConfigRecord]:
        """全部配置, 按策略键稳定排序"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.strategy_configs).order_by(
                    schema.strategy_configs.c.strategy_key
                )
            ).mappings().all()
        return [StrategyConfigRecord.model_validate(dict(row)) for row in rows]
