import os

import dotenv

import log
from client.binance_client import BinanceSwapClient
from template.top10_short import RankingType, place_top10_short_orders

dotenv.load_dotenv()

logger = log.getLogger(__name__)


def create_binance_client(client_type: str) -> BinanceSwapClient:
    api_key = os.environ.get(f"BINANCE_API_KEY_{client_type.upper()}")
    api_secret = os.environ.get(f"BINANCE_API_SECRET_{client_type.upper()}")
    is_test = os.environ.get(f"BINANCE_IS_TEST_{client_type.upper()}") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")

    logger.info("api_key: %s*****, api_secret: %s*****, is_test: %s", api_key[:5], api_secret[:5], is_test)
    return BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)


if __name__ == "__main__":
    main_binance_client = create_binance_client("main")
    ranking_type = os.environ.get("TOP10_SHORT_RANKING_TYPE", RankingType.LOSERS.value)
    success_orders, failed_orders = place_top10_short_orders(
        exchange_client=main_binance_client,
        per_symbol_usdt=100.0,
        top_n=10,
        ranking_type=ranking_type,
    )

    logger.info("submit done success=%s failed=%s", len(success_orders), len(failed_orders))
    if failed_orders:
        logger.warning("failed orders=%s", failed_orders)
