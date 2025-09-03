import os

from client.binance_chaser_order import LimitOrderChaser
from client.binance_client import BinanceSwapClient
import log
from model import Symbol
from model import OrderSide

logger = log.getLogger(__name__)

if __name__ == '__main__':
    api_key = os.environ.get('BINANCE_API_KEY')
    api_secret = os.environ.get('BINANCE_API_SECRET')
    is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
    else:
        logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    binance_client = BinanceSwapClient(
        api_key=api_key,
        api_secret=api_secret,
        is_test=is_test,
    )
    chaser = LimitOrderChaser(
        client=binance_client,
        symbol=Symbol('BTC', 'USDT'),
        side=OrderSide.BUY,
        quantity=0.001,
    )
    chaser.run()