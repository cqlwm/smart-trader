"""统一入口: 控制台 API + 策略运行时

- 控制台 API 负责只读账本、策略配置、控制命令提交与行情展示;
- 策略运行时与控制台同进程, 按每个启用的策略配置自身的 `data_source`
  决定使用 WebSocket 推送还是 REST 定时拉取;
- 没有启用配置或策略运行时装配失败时, 控制台仍然可用 (记录日志并继续)。

运行:
- 常规: `uv run python run.py`
- 开发热重载: `uv run python run.py --reload`（或
  `uv run uvicorn run:create_application --factory --reload --port 8001`）
"""

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import dotenv
import uvicorn
from fastapi import FastAPI

from bootstrap import create_binance_exchange, create_public_market_data
from smart_trader import log
from smart_trader.api.app import create_app as build_api_app
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

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def _cors_origins() -> list[str]:
    """允许的浏览器来源（逗号分隔, 默认本地 Vite 开发服务器）"""
    raw = os.environ.get("SMART_TRADER_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def reload_requested(argv: Sequence[str]) -> bool:
    """命令行是否请求 uvicorn 开发热重载"""
    return "--reload" in argv


def create_application() -> FastAPI:
    """构建控制台 + 策略运行时（uvicorn 工厂入口）

    `uvicorn --reload` 以 `run:create_application --factory` 加载: 每个 reload
    子进程都会重新执行本函数, 账本句柄与策略运行时随进程重建; 旧进程退出时
    lifespan shutdown 钩子先停策略运行时, 再关闭账本, 保证持仓从账本恢复。
    """
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
        # 运行中轮询配置: 新增策略与调参无需重启进程
        configs_provider=config_store.list,
        # 停用流程需要回写生命周期状态
        lifecycle_store=config_store,
    )

    def shutdown() -> None:
        """先停策略运行时, 再释放账本句柄"""
        try:
            runtime.stop()
        finally:
            reader.close()
            ack_store.close()
            config_store.close()
            commands.close()
            journal.close()

    return build_api_app(
        reader,
        commands,
        token=os.environ.get("SMART_TRADER_API_TOKEN"),
        cors_origins=_cors_origins(),
        static_dir=static_dir if (static_dir / "index.html").is_file() else None,
        runtime_path=registry.path,
        market_provider=MarketDataProvider(create_public_market_data),
        config_store=config_store,
        ack_store=ack_store,
        on_startup=runtime.start,
        on_shutdown=shutdown,
    )


def main() -> None:
    host = os.environ.get("SMART_TRADER_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SMART_TRADER_API_PORT", "8000"))
    logger.info("控制台与策略运行时启动: http://%s:%s", host, port)

    if reload_requested(sys.argv[1:]):
        logger.info("开发热重载已开启: 修改 .py 文件将自动重启进程")
        uvicorn.run(
            "run:create_application",
            factory=True,
            host=host,
            port=port,
            reload=True,
        )
        return

    uvicorn.run(create_application(), host=host, port=port)


if __name__ == "__main__":
    main()
