"""控制命令 API 服务: 命令视图与分页"""

from smart_trader.api.schemas import CommandPage, CommandView
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.records import CommandRecord


def command_view(record: CommandRecord) -> CommandView:
    return CommandView(
        id=record.id,
        strategy_key=record.strategy_key,
        action=record.action,
        status=record.status,
        result=record.result,
        requested_by=record.requested_by,
        requested_at=record.requested_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def list_command_views(
    store: CommandStore,
    *,
    strategy_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CommandPage:
    records = store.list_commands(
        strategy_key=strategy_key, limit=limit, offset=offset
    )
    total = store.count_commands(strategy_key=strategy_key)
    return CommandPage(
        items=[command_view(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_command_view(
    store: CommandStore, command_id: str
) -> CommandView | None:
    record = store.get_command(command_id)
    if record is None:
        return None
    return command_view(record)
