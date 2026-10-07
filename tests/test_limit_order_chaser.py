import os

import pytest

from smart_trader import log
from smart_trader.client.binance_client import BinanceSwapClient
from smart_trader.model import OrderSide, PlaceOrderBehavior, Symbol

logger = log.getLogger(__name__)


@pytest.mark.integration
def test_limit_order_chaser():
    """集成测试：对 Binance 测试网执行一次追单"""
    api_key = os.environ.get("BINANCE_API_KEY_MAIN")
    api_secret = os.environ.get("BINANCE_API_SECRET_MAIN")
    is_test = os.environ.get("BINANCE_IS_TEST_MAIN") == "True"
    if not api_key or not api_secret:
        pytest.skip("BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN 未设置")
    logger.info(
        f"api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}"
    )

    binance_client = BinanceSwapClient(
        api_key=api_key, api_secret=api_secret, is_test=is_test
    )
    chaser = binance_client.create_chaser(
        symbol=Symbol(base="DOGE", quote="USDT"),
        order_side=OrderSide.BUY,
        quantity=102,
        position_side="Long",
        place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
    )
    chaser.run()
