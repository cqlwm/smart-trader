from argparse import ArgumentError

from plans import bybit_swap_plan, okx_swap_plan, binance_plan
from strategy import simple_grid_strategy
import os
from utils import log
from klines import KlineStream

logger = log.build_logger('Application')

if __name__ == '__main__':
    strategy_name = os.getenv('START_STRATEGY', 'simple_grid_strategy')
    if strategy_name:
        logger.info(f'start strategy name : {strategy_name}')
    else:
        raise ArgumentError(None, 'strategy name not defined')
    
    if strategy_name == 'simple_grid_strategy':
        simple_grid_strategy.main()
    else:
        ks = KlineStream()
        if strategy_name == 'bybit':
            bybit_swap_plan.execution(ks)
        if strategy_name == 'okx':
            okx_swap_plan.execution(ks)
        if strategy_name == 'binance':
            binance_plan.execution(ks)
        ks.start()
