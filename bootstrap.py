"""启动装配: run.py 与 run_timer.py 共享的交易所客户端构建

入口脚本只保留 main() 薄层, 凭据读取与日志在此统一(单一来源)。
"""

import os

from smart_trader import log
from smart_trader.client.binance_client import BinanceSwapClient

logger = log.getLogger(__name__)


def create_binance_client(client_type: str) -> BinanceSwapClient:
    """
    按类型读取环境变量凭据并构建客户端(构造时触网加载市场, 仅供入口调用)
    @param client_type: 凭据环境变量后缀(main/copy), 如 BINANCE_API_KEY_MAIN
    @raise ValueError: 凭据缺失
    """
    api_key = os.environ.get(f"BINANCE_API_KEY_{client_type.upper()}")
    api_secret = os.environ.get(f"BINANCE_API_SECRET_{client_type.upper()}")
    is_test = os.environ.get(f"BINANCE_IS_TEST_{client_type.upper()}") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
    logger.info(
        f"api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}"
    )
    return BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)
