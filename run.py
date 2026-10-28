from pathlib import Path

import dotenv

from bootstrap import create_binance_exchange
from smart_trader import log
from smart_trader.data_event_loop import BinanceDataEventLoop
from smart_trader.exchange.ports import Exchange
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
        data_source=DataSource.WS,
        exchange=main_exchange,
        journal=journal,
    )
    command_runner = CommandRunner(
        command_store,
        controllable_strategies(tasks),
        registry=RuntimeRegistry(Path(DB_PATH).parent / "runtime.json"),
    )

    kline_subscribes: list[str] = []
    data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

    for task in tasks:
        for timeframe in task.strategy.timeframes:
            sub_key = task.symbol.binance_ws_sub_kline(timeframe)
            if sub_key not in kline_subscribes:
                kline_subscribes.append(sub_key)
        data_event_loop.add_task(task)

    command_runner.start()
    try:
        # 阻塞运行; 连接断开时由 start() 内部自动重连
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
