from client.binance_chaser_order import LimitOrderChaser
import dotenv
import os
from client.binance_client import BinanceSwapClient
import log
from model import OrderSide, PlaceOrderBehavior, Symbol

dotenv.load_dotenv()

logger = log.getLogger(__name__)

api_key = os.environ.get('BINANCE_API_KEY')
api_secret = os.environ.get('BINANCE_API_SECRET')
is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
if not api_key or not api_secret:
    raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
else:
    logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)

def test_limit_order_chaser():
    chaser = LimitOrderChaser(
        client=binance_client,
        symbol=Symbol(base='DOGE', quote='USDT'),
        side=OrderSide.BUY,
        quantity=102,
        tick_size=0.0001,
        position_side='Long',
        place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
    )
    chaser.run()


if __name__ == '__main__':
    test_limit_order_chaser()
