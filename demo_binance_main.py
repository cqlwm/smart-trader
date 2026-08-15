import log
from run import main_binance_client
from client.binance_client import BinanceSwapClient
from model import Symbol

logger = log.getLogger(__name__)

BTCUSDT = Symbol(base='BTC', quote='USDT')


def check_instance() -> None:
    assert isinstance(main_binance_client, BinanceSwapClient), 'main_binance_client 类型错误'
    logger.info(f'实例类型: {type(main_binance_client).__name__}')
    logger.info(f'exchange_name: {main_binance_client.exchange_name}')


def check_symbol_info() -> None:
    info = main_binance_client.symbol_info(BTCUSDT)
    logger.info(
        f'{BTCUSDT.binance()} tick_size={info.tick_size} step_size={info.step_size} '
        f'min_qty={info.min_qty} price_precision={info.price_precision()} qty_precision={info.qty_precision()}'
    )


def check_ticker() -> None:
    ticker = main_binance_client.exchange.fetch_ticker(BTCUSDT.ccxt())
    logger.info(f'BTC/USDT 最新价: {ticker["last"]}')


def check_balance() -> None:
    usdt = main_binance_client.balance('USDT')
    logger.info(f'USDT 可用余额: {usdt}')


def check_positions() -> None:
    positions = main_binance_client.positions()
    logger.info(f'当前持仓数量: {len(positions)}')


if __name__ == '__main__':
    check_instance()
    check_symbol_info()
    check_ticker()
    check_balance()
    check_positions()
    logger.info('main_binance_client 使用正常')
