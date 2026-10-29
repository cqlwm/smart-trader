"""人工外部仓位声明仓储（position_acks 表）

- 只由 API 进程写; 策略进程不读取, 不改变账本与交易;
- 同一策略同时最多一条 active: 新建声明会软撤销旧声明（保留历史）;
- 撤销是软删除且幂等, 不提供物理删除;
- 表不存在时响亮报错并指向迁移脚本。
"""

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Connection

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import PositionAckStatus, PositionSide
from smart_trader.storage import schema
from smart_trader.storage.engine import create_engine_for_path
from smart_trader.storage.records import PositionAckRecord


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PositionAckNotFoundError(SmartTraderError):
    def __init__(self, ack_id: str):
        super().__init__(
            ErrorCode.POSITION_ACK_NOT_FOUND,
            f"人工仓位声明不存在: {ack_id}",
        )
        self.ack_id = ack_id


class PositionAckStore:
    """人工外部仓位声明的读写仓储（API 进程持有）"""

    def __init__(self, db_path: str):
        self._engine = create_engine_for_path(db_path)
        with self._engine.connect() as conn:
            self._require_table(conn)

    @staticmethod
    def _require_table(conn: Connection) -> None:
        existing = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='position_acks'"
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ValueError(
                "position_acks 表不存在; 请执行 "
                "scripts/migrations/005_add_position_acks.py <db_path>"
            )

    def close(self) -> None:
        self._engine.dispose()

    def create(
        self,
        *,
        strategy_key: str,
        symbol: str,
        position_side: PositionSide,
        external_qty: float,
        note: str = "",
        acknowledged_by: str = "web",
    ) -> PositionAckRecord:
        """新建声明并撤销同策略的旧 active 声明"""
        key = strategy_key.strip()
        if not key:
            raise ValueError("strategy_key 不能为空")
        if external_qty <= 0:
            raise ValueError("external_qty 必须大于 0")
        now = _now()
        record = PositionAckRecord(
            id=f"ACK{secrets.token_hex(nbytes=5)}",
            strategy_key=key,
            symbol=symbol,
            position_side=position_side,
            external_qty=external_qty,
            note=note,
            status=PositionAckStatus.ACTIVE,
            acknowledged_by=acknowledged_by or "web",
            acknowledged_at=now,
            updated_at=now,
        )
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.position_acks)
                .where(
                    schema.position_acks.c.strategy_key == key,
                    schema.position_acks.c.status
                    == PositionAckStatus.ACTIVE.value,
                )
                .values(
                    status=PositionAckStatus.REVOKED.value,
                    revoked_at=now,
                    revoked_by=record.acknowledged_by,
                    updated_at=now,
                )
            )
            conn.execute(
                insert(schema.position_acks).values(
                    **record.model_dump(mode="json")
                )
            )
        return record

    def get(self, ack_id: str) -> PositionAckRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.position_acks).where(
                    schema.position_acks.c.id == ack_id
                )
            ).mappings().first()
        if row is None:
            return None
        return PositionAckRecord.model_validate(dict(row))

    def active_for(self, strategy_key: str) -> PositionAckRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.position_acks)
                .where(
                    schema.position_acks.c.strategy_key == strategy_key,
                    schema.position_acks.c.status
                    == PositionAckStatus.ACTIVE.value,
                )
                .order_by(
                    schema.position_acks.c.acknowledged_at.desc(),
                    text("rowid DESC"),
                )
                .limit(1)
            ).mappings().first()
        if row is None:
            return None
        return PositionAckRecord.model_validate(dict(row))

    def list_active(self) -> list[PositionAckRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.position_acks)
                .where(
                    schema.position_acks.c.status
                    == PositionAckStatus.ACTIVE.value
                )
                .order_by(
                    schema.position_acks.c.acknowledged_at.desc(),
                    text("rowid DESC"),
                )
            ).mappings().all()
        return [PositionAckRecord.model_validate(dict(row)) for row in rows]

    def list(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[PositionAckRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.position_acks)
                .order_by(
                    schema.position_acks.c.acknowledged_at.desc(),
                    text("rowid DESC"),
                )
                .limit(limit)
                .offset(offset)
            ).mappings().all()
        return [PositionAckRecord.model_validate(dict(row)) for row in rows]

    def count(self) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(schema.position_acks)
            ).scalar_one()
        return int(total)

    def revoke(
        self, ack_id: str, *, revoked_by: str = "web"
    ) -> PositionAckRecord:
        """软撤销; 已撤销时幂等返回当前行"""
        existing = self.get(ack_id)
        if existing is None:
            raise PositionAckNotFoundError(ack_id)
        if existing.status is PositionAckStatus.REVOKED:
            return existing
        now = _now()
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.position_acks)
                .where(
                    schema.position_acks.c.id == ack_id,
                    schema.position_acks.c.status
                    == PositionAckStatus.ACTIVE.value,
                )
                .values(
                    status=PositionAckStatus.REVOKED.value,
                    revoked_at=now,
                    revoked_by=revoked_by or "web",
                    updated_at=now,
                )
            )
        revoked = self.get(ack_id)
        if revoked is None:
            raise PositionAckNotFoundError(ack_id)
        return revoked
