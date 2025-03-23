from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.alpha_trend_signal.alpha_trend_signal_v3 import AlphaTrendSignalV3

from client.bybit_client import BybitSwapClient
from strategy.grids_strategy import SwapGridStrategy
from strategy import OrderSide
from klines import KlineStream

# ZELM7qwIuxehP9yMCC  /   me3sz370az1E0hHAH6SeiqqQoDfrZ31s2VuT
bybit_client = BybitSwapClient('ZELM7qwIuxehP9yMCC', 'me3sz370az1E0hHAH6SeiqqQoDfrZ31s2VuT', False)

open_position_price = 0.36596

def execution(ks: KlineStream):
    ks.subscribes(key='dogeusdt@kline_5m',strategies=[open_long1(), open_long2()])

def open_long1():
    strategy = SwapGridStrategy(ex_client=bybit_client,
                                symbol='DOGEUSDT',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='bybit_swap_grid_long_buy_dogeusdt_01',
                                per_qty=50,
                                take_profit_rate=0.1)
    # strategy.max_order = 20
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.003
    strategy.min_profit_rate = 0.003
    strategy.upper_limit = open_position_price
    strategy.signal = AlphaTrendSignal(strategy.master_side.value)
    return strategy

def open_long2():
    strategy = SwapGridStrategy(ex_client=bybit_client,
                                symbol='DOGEUSDT',
                                position_side='long',
                                master_side=OrderSide.SELL,
                                strategy_key='bybit_swap_grid_long_sell_dogeusdt_01',
                                per_qty=25,
                                take_profit_rate=0.1)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.003
    strategy.min_profit_rate = 0.003
    strategy.lower_limit = open_position_price
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy


def _test():
    print('run bybit_swap_grid_long_buy_dogeusdt_01')
    global bybit_client
    bybit_client = BybitSwapClient('g0TfHnDyxZOWeHLkU5', 'ZobzYT4Wr70mCPdRDMcCPPAvMID5o8r3e5X7', True)
    ks = KlineStream()
    execution(ks)
    ks.start()


if __name__ == '__main__':
    _test()
