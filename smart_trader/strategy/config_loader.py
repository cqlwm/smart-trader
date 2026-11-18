"""按数据库配置装配策略任务（P3）

统一入口按每个配置行的 `data_source` 分别调用本加载器：`ws` 交给 WebSocket
事件循环、`timer` 交给定时拉取事件循环。启动时批量装配；运行中的热新增由
`StrategyRuntime` 对单条配置复用本加载器（见 .memory/01 第十五节）。

身份字段变更与启停仍要求重启：本加载器只负责构造新任务，不替换运行中的
策略实例。

空集合返回空列表: 统一入口以控制台为宿主, 没有可运行策略时仍须提供
配置与账本查看能力; 是否告警由调用方（`StrategyRuntime`）决定。
"""

from collections.abc import Iterable

from smart_trader.exchange.ports import Exchange
from smart_trader.model import DataSource
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import StrategyConfigRecord
from smart_trader.strategy.config_payload import StrategyConfigPayload
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from smart_trader.task.strategy_task import StrategyTask


def build_strategy_tasks(
    records: Iterable[StrategyConfigRecord],
    *,
    data_source: DataSource,
    exchange: Exchange,
    journal: OrderJournal,
) -> list[StrategyTask]:
    """把启用且数据源匹配的配置行装配为 StrategyTask 列表

    @param records 配置行（顺序即加载顺序, 便于复现）
    @param data_source 要装配的数据源类型
    @param exchange 交易 / 行情 / 账户端口
    @param journal 账本（策略据此恢复活跃仓位）
    @return 匹配 data_source 的启用策略任务; 没有匹配项时为空列表
    @raise ValueError 载荷非法
    """
    tasks: list[StrategyTask] = []
    for record in records:
        if not record.enabled:
            continue
        try:
            payload = StrategyConfigPayload.from_json(record.payload)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"策略配置 {record.strategy_key} 无效: {exc}"
            ) from exc
        if payload.data_source is not data_source:
            continue
        config = payload.to_strategy_config(record.strategy_key)
        strategy = SignalGridStrategy(
            config,
            trading=exchange,
            market=exchange,
            journal=journal,
            account=exchange,
        )
        tasks.append(StrategyTask(symbol=config.symbol, strategy=strategy))
    return tasks
