"""控制台 API 入口

独立进程读取策略进程同一个 sqlite 账本（只发 SELECT）, 控制命令只写 `commands` 表,
不写仓位 / 订单、不调交易所。前端构建产物存在时一并托管 (web/dist)。
"""

import os
from pathlib import Path

import dotenv
import uvicorn

from smart_trader import log
from smart_trader.api.app import create_app
from smart_trader.config import DB_PATH
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.config_store import ConfigStore
from smart_trader.storage.position_ack_store import PositionAckStore
from smart_trader.storage.reader import OrderJournalReader

dotenv.load_dotenv()

log.init_logging()

logger = log.getLogger(__name__)


def main() -> None:
    from bootstrap import create_public_market_data
    from smart_trader.api.market_provider import MarketDataProvider

    static_dir = Path(__file__).resolve().parent / "web" / "dist"
    reader = OrderJournalReader(DB_PATH)
    commands = CommandStore(DB_PATH)
    config_store = ConfigStore(DB_PATH)
    ack_store = PositionAckStore(DB_PATH)
    market_provider = MarketDataProvider(create_public_market_data)
    app = create_app(
        reader,
        commands,
        token=os.environ.get("SMART_TRADER_API_TOKEN"),
        static_dir=static_dir if (static_dir / "index.html").is_file() else None,
        runtime_path=Path(DB_PATH).parent / "runtime.json",
        market_provider=market_provider,
        config_store=config_store,
        ack_store=ack_store,
    )
    host = os.environ.get("SMART_TRADER_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SMART_TRADER_API_PORT", "8000"))
    logger.info("控制台 API 启动: http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
