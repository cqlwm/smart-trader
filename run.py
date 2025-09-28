import log
import os
from data_event_loop import BinanceDataEventLoop
from client.binance_client import BinanceSwapClient
import dotenv

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def dev_debug():
    node_env = os.environ.get('NODE_ENV', 'NONE')
    logger.info(f'NODE_ENV: {node_env}')
    if node_env != 'DEV':
        return

if __name__ == '__main__':
    api_key = os.environ.get('BINANCE_API_KEY')
    api_secret = os.environ.get('BINANCE_API_SECRET')
    is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')
    else:
        logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)

    # from template import long_short_rotation
    # task: Task = long_short_rotation.template(binance_client, symbol, timeframe)
    from template import simple_grid_doge
    template_model = simple_grid_doge.template(binance_client)

    data_event_loop = BinanceDataEventLoop(
        kline_subscribes=[
            template_model.symbol.binance_ws_sub_kline(template_model.timeframe)
        ]
    )
    data_event_loop.add_task(template_model.strategy_task)

    data_event_loop.start()



