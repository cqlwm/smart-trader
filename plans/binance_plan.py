from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.none_signal.none_signal import NoneSignal

from client.binance_client import BinanceSwapClient
from strategy.grids_strategy import SwapGridStrategy
from strategy import OrderSide
from klines import KlineStream

binance_client = BinanceSwapClient(
    api_key="crem6s2RAVCeD3VqmVrpbTduNYpPy8SY346Tg3DhzBJmdBxjdK4snk3jjRQL789M",
    api_secret="6m1H8d4wfetfm6ddZGFD5vWpEIyDIut50BXSaddfoYTd2gzpynaTSy7ZKrEB9FWJ",
)

long_open_price = 0.272287
short_open_price = 0.202144
avg_price = (long_open_price + short_open_price) / 2

def execution(ks: KlineStream):
    ks.subscribes(key='dogeusdt@kline_1m', strategies=[sell_short(), ])
    # buy_short(), buy_long(), sell_long()
    # ks.subscribes(key='bnbusdt@kline_1m', strategies=[buy_long_bnb()])

# 卖出开空
def sell_short():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDC',
                                position_side='short',
                                master_side=OrderSide.SELL,
                                strategy_key='binance_dogeusdc_swap_grid_short_sell_01',
                                per_qty=50,
                                take_profit_rate=0.01)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.0005
    strategy.min_profit_rate = 0.003
    # strategy.lower_limit = avg_price
    # strategy.upper_limit = long_open_price
    strategy.signal = AlphaTrendSignal(strategy.master_side.reversal().value)
    # strategy.signal = NoneSignal(strategy.master_side.value)
    return strategy

# 买入平空
def buy_short():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDT',
                                position_side='short',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_dogeusdt_swap_grid_short_buy_01',
                                per_qty=100,
                                take_profit_rate=0.01)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.0001
    strategy.min_profit_rate = 0.003
    strategy.upper_limit = short_open_price
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

# 买入开多
def buy_long():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDT',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_dogeusdt_swap_grid_long_buy_01',
                                per_qty=50,
                                take_profit_rate=0.01)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.0005
    strategy.min_profit_rate = 0.003
    strategy.upper_limit = avg_price
    strategy.lower_limit = short_open_price
    strategy.signal = AlphaTrendSignal(strategy.master_side.value)
    return strategy

# 卖出平多
def sell_long():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDT',
                                position_side='long',
                                master_side=OrderSide.SELL,
                                strategy_key='binance_dogeusdt_swap_grid_long_sell_01',
                                per_qty=50,
                                take_profit_rate=0.01)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.0005
    strategy.min_profit_rate = 0.003
    strategy.lower_limit = long_open_price
    strategy.signal = AlphaTrendSignal(strategy.master_side.value)
    return strategy


# 买入开多
def buy_long_bnb():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='BNB/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_bnbusdt_swap_grid_long_buy_01',
                                per_qty=0.01,
                                take_profit_rate=0.01)
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.03
    strategy.min_profit_rate = 0.005
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy


def _test():
    print('run binance test')
    ks = KlineStream()
    execution(ks)
    ks.start()


if __name__ == '__main__':
    _test()
