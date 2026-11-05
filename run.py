"""统一入口: 控制台 API + 策略运行时

- 控制台 API 负责只读账本、策略配置、控制命令提交与行情展示;
- 策略运行时与控制台同进程, 按每个启用的策略配置自身的 `data_source`
  决定使用 WebSocket 推送还是 REST 定时拉取;
- 没有启用配置或策略运行时装配失败时, 控制台仍然可用 (记录日志并继续)。

运行: uv run python run.py
"""

import os
from pathlib import Path

import dotenv
import uvicorn

from bootstrap import create_binance_exchange, create_public_market_data
from smart_trader import log
from smart_trader.api.app import create_app
from smart_trader.api.market_provider import MarketDataProvider
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.config_store import ConfigStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.position_ack_store import PositionAckStore
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.strategy_runtime import StrategyRuntime

dotenv.load_dotenv()

log.init_logging()

logger = log.getLogger(__name__)


def main() -> None:
    from smart_trader.config import DB_PATH

    db_path = Path(DB_PATH)
    static_dir = Path(__file__).resolve().parent / "web" / "dist"

    # OrderJournal 先建库: schema.py 是当前结构的唯一声明, 其余仓储随后打开
    journal = OrderJournal(DB_PATH)
    commands = CommandStore(DB_PATH)
    config_store = ConfigStore(DB_PATH)
    ack_store = PositionAckStore(DB_PATH)
    reader = OrderJournalReader(DB_PATH)
    registry = RuntimeRegistry(db_path.parent / "runtime.json")

    runtime = StrategyRuntime(
        config_store.list(),
        exchange_factory=lambda: create_binance_exchange("main"),
        journal=journal,
        command_store=commands,
        registry=registry,
    )

    app = create_app(
        reader,
        commands,
        token=os.environ.get("SMART_TRADER_API_TOKEN"),
        static_dir=static_dir if (static_dir / "index.html").is_file() else None,
        runtime_path=registry.path,
        market_provider=MarketDataProvider(create_public_market_data),
        config_store=config_store,
        ack_store=ack_store,
        on_startup=runtime.start,
        on_shutdown=runtime.stop,
    )

    host = os.environ.get("SMART_TRADER_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SMART_TRADER_API_PORT", "8000"))
    logger.info("控制台与策略运行时启动: http://%s:%s", host, port)
    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        runtime.stop()
        reader.close()
        ack_store.close()
        config_store.close()
        commands.close()
        journal.close()


if __name__ == "__main__":
    main()
