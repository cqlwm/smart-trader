"""控制命令仓储（commands 表）

- 只写 `commands`, 不碰 `grid_positions` / `orders` —— 账本唯一写者仍是策略进程;
- 状态机: pending → running → succeeded | failed;
- 认领用条件 UPDATE, 保证同一命令只会被一个执行者取走;
- `idempotency_key` 唯一索引用于前端防重复提交。

表不存在时响亮报错并指向迁移脚本, 不在业务代码里自动建表。
"""

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from smart_trader.model import CommandAction, CommandStatus
from smart_trader.storage import schema
from smart_trader.storage.engine import create_engine_for_path
from smart_trader.storage.records import CommandRecord

_MAX_ID_RETRY = 5


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CommandStore:
    """控制命令的提交端与执行端共用仓储"""

    def __init__(self, db_path: str):
        self._engine = create_engine_for_path(db_path)
        with self._engine.connect() as conn:
            self._require_commands_table(conn)

    @staticmethod
    def _require_commands_table(conn: Connection) -> None:
        existing = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='commands'"
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ValueError(
                "commands 表不存在; 请执行 "
                "scripts/migrations/002_add_commands.py <db_path>"
            )

    def close(self) -> None:
        self._engine.dispose()

    # ---- 提交 ----

    def submit(
        self,
        *,
        action: CommandAction,
        strategy_key: str | None,
        requested_by: str = "web",
        idempotency_key: str | None = None,
    ) -> CommandRecord:
        """写入一条 pending 命令; 幂等键冲突时返回已有命令"""
        self._validate_scope(action, strategy_key)
        key = idempotency_key or None

        for _ in range(_MAX_ID_RETRY):
            now = _now()
            record = CommandRecord(
                id=f"CMD{secrets.token_hex(nbytes=5)}",
                strategy_key=strategy_key,
                action=action,
                status=CommandStatus.PENDING,
                requested_by=requested_by or "web",
                idempotency_key=key,
                requested_at=now,
                updated_at=now,
            )
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        insert(schema.commands).values(
                            **record.model_dump(mode="json")
                        )
                    )
            except IntegrityError:
                if key is not None:
                    existing = self._get_by_idempotency_key(key)
                    if existing is not None:
                        return existing
                continue
            return record
        raise RuntimeError(f"连续 {_MAX_ID_RETRY} 次未能写入唯一命令")

    @staticmethod
    def _validate_scope(action: CommandAction, strategy_key: str | None) -> None:
        if action == CommandAction.KILL_SWITCH:
            if strategy_key is not None:
                raise ValueError("kill_switch 必须是全局命令(strategy_key 为空)")
        elif not strategy_key:
            raise ValueError(f"{action.value} 必须指定 strategy_key")

    def _get_by_idempotency_key(self, key: str) -> CommandRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.commands).where(
                    schema.commands.c.idempotency_key == key
                )
            ).mappings().first()
        if row is None:
            return None
        return CommandRecord.model_validate(dict(row))

    # ---- 认领 / 完成 ----

    def claim_next(self) -> CommandRecord | None:
        """认领最早的一条 pending 命令; 无命令或竞争失败返回 None"""
        with self._engine.begin() as conn:
            row = conn.execute(
                select(schema.commands)
                .where(schema.commands.c.status == CommandStatus.PENDING.value)
                .order_by(schema.commands.c.requested_at, text("rowid"))
                .limit(1)
            ).mappings().first()
            if row is None:
                return None

            now = _now()
            command_id = str(row["id"])
            result = conn.execute(
                update(schema.commands)
                .where(
                    schema.commands.c.id == command_id,
                    schema.commands.c.status == CommandStatus.PENDING.value,
                )
                .values(
                    status=CommandStatus.RUNNING.value,
                    started_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            data = dict(row)
            data.update(
                status=CommandStatus.RUNNING.value,
                started_at=now,
                updated_at=now,
            )
        return CommandRecord.model_validate(data)

    def complete(
        self,
        command_id: str,
        *,
        status: CommandStatus,
        result: str | None = None,
    ) -> None:
        """写入终态与结果; 只允许 succeeded / failed"""
        if not status.is_finished():
            raise ValueError(f"complete 只接受终态, 实际: {status.value}")
        now = _now()
        with self._engine.begin() as conn:
            updated = conn.execute(
                update(schema.commands)
                .where(
                    schema.commands.c.id == command_id,
                    schema.commands.c.status == CommandStatus.RUNNING.value,
                )
                .values(
                    status=status.value,
                    result=result,
                    finished_at=now,
                    updated_at=now,
                )
            )
            if updated.rowcount != 1:
                raise ValueError(
                    f"命令 {command_id} 不在 running 状态, 无法完成"
                )

    # ---- 查询 ----

    def get_command(self, command_id: str) -> CommandRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.commands).where(schema.commands.c.id == command_id)
            ).mappings().first()
        if row is None:
            return None
        return CommandRecord.model_validate(dict(row))

    def list_commands(
        self,
        *,
        strategy_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CommandRecord]:
        stmt = select(schema.commands)
        if strategy_key is not None:
            stmt = stmt.where(schema.commands.c.strategy_key == strategy_key)
        stmt = (
            stmt.order_by(
                schema.commands.c.requested_at.desc(), text("rowid DESC")
            )
            .limit(limit)
            .offset(offset)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [CommandRecord.model_validate(dict(row)) for row in rows]

    def count_commands(self, *, strategy_key: str | None = None) -> int:
        stmt = select(func.count()).select_from(schema.commands)
        if strategy_key is not None:
            stmt = stmt.where(schema.commands.c.strategy_key == strategy_key)
        with self._engine.connect() as conn:
            total = conn.execute(stmt).scalar_one()
        return int(total)
