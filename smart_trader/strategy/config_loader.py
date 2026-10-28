"""按数据库配置装配策略任务（P3）

`run.py` 只加载 `data_source=ws`，`run_timer.py` 只加载 `data_source=timer`；
配置在进程启动时读取一次，运行中不热替换（见 .memory/01 第十五节）。

空集合直接报错: 没有任何任务的策略进程没有存在意义, 启动即失败比静默空跑安全。
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
    @param data_source 本进程入口的行情驱动方式
    @param exchange 交易 / 行情 / 账户端口
    @param journal 账本（策略据此恢复活跃仓位）
    @raise ValueError 载荷非法或没有任何可用配置
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
    if not tasks:
        raise ValueError(
            f"没有启用的 {data_source.value} 策略配置; "
            "请检查 strategy_configs 或执行种子脚本"
        )
    return tasks
