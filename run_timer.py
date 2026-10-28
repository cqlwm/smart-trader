"""定时拉取型入口: 不建 WebSocket, 按 timeframe 节奏经 REST 拉取K线驱动策略

与 run.py(WebSocket 推送)互为替代, 装配同一套 exchange/template/journal。
调度语义(周期边界触发 on_kline_finished / 周期中点触发 on_kline)
见 .memory/01-system-architecture.md 第九节。

运行: uv run python run_timer.py
"""

from pathlib import Path

import dotenv

from bootstrap import create_binance_exchange
from smart_trader import log
from smart_trader.data_event_loop import TimerDataEventLoop
from smart_trader.exchange.ports import Exchange
from smart_trader.model import Symbol
from smart_trader.task.strategy_task import StrategyTask

dotenv.load_dotenv()

log.init_logging()

logger = log.getLogger(__name__)

# main binance exchange
main_exchange: Exchange = create_binance_exchange("main")


def main():
    from smart_trader.config import DB_PATH
    from smart_trader.model import DataSource
    from smart_trader.runtime import RuntimeRegistry
    from smart_trader.storage.command_store import CommandStore
    from smart_trader.storage.config_store import ConfigStore
    from smart_trader.storage.order_journal import OrderJournal
    from smart_trader.strategy.config_loader import build_strategy_tasks
    from smart_trader.task.command_runner import (
        CommandRunner,
        controllable_strategies,
    )

    journal = OrderJournal(DB_PATH)
    command_store = CommandStore(DB_PATH)
    config_store = ConfigStore(DB_PATH)
    tasks: list[StrategyTask] = build_strategy_tasks(
        config_store.list(),
        data_source=DataSource.TIMER,
        exchange=main_exchange,
        journal=journal,
    )
    command_runner = CommandRunner(
        command_store,
        controllable_strategies(tasks),
        registry=RuntimeRegistry(Path(DB_PATH).parent / "runtime.json"),
    )

    # 构造期校验非空, 先收集订阅再建循环
    subscriptions: list[tuple[Symbol, str]] = []
    for task in tasks:
        for timeframe in task.strategy.timeframes:
            subscription = (task.symbol, timeframe)
            if subscription not in subscriptions:
                subscriptions.append(subscription)

    data_event_loop = TimerDataEventLoop(
        subscriptions=subscriptions, market=main_exchange
    )
    for task in tasks:
        data_event_loop.add_task(task)

    command_runner.start()
    try:
        # 阻塞调度; 到点拉取K线并分发
        data_event_loop.start()
    except KeyboardInterrupt:
        logger.info("收到退出信号, 停止事件循环")
    finally:
        data_event_loop.stop()
        command_runner.stop()
        config_store.close()
        command_store.close()


if __name__ == "__main__":
    main()
