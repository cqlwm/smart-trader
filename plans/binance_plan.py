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

def execution(ks: KlineStream):
    ks.subscribes(key='dogeusdc@kline_1m', strategies=[buy_long_dogeusdc()])
    ks.subscribes(key='dogeusdc@kline_1m', strategies=[sell_short_dogeusdc()])
    ks.subscribes(key='bnbusdc@kline_5m', strategies=[buy_long_bnbusdc()])
    ks.subscribes(key='btcusdc@kline_5m', strategies=[buy_long_btcusdc()])
    ks.subscribes(key='ethusdc@kline_5m', strategies=[buy_long_ethusdc()])
    # ks.subscribes(key='xrpusdc@kline_5m', strategies=[buy_long_xrpusdc()])

# 买入开多
def buy_long_dogeusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_dogeusdc_swap_grid_long_buy_02',
                                per_qty=100,
                                take_profit_rate=0.01)
    strategy.max_order = 0
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.0001
    strategy.min_profit_rate = 0.005
    # strategy.upper_limit = 0.192214
    # strategy.lower_limit = 0.159634
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

# dogeusdc sell short
def sell_short_dogeusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='DOGE/USDC',
                                position_side='short',
                                master_side=OrderSide.SELL,
                                strategy_key='binance_dogeusdc_swap_grid_short_sell_02',
                                per_qty=100,
                                take_profit_rate=0.01)
    strategy.max_order = 15
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.001
    strategy.min_profit_rate = 0.005
    # 多头与空头盈亏平衡价的均价
    strategy.lower_limit = 0.172230
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

def buy_long_btcusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='BTC/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_btcusdc_swap_grid_long_buy_01',
                                per_qty=0.001,
                                take_profit_rate=0.01)
    strategy.max_order = 0
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.002
    strategy.min_profit_rate = 0.005
    # strategy.lower_limit = 100000
    # strategy.signal = AlphaTrendSignal(strategy.master_side.reversal().value)
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

# long buy bnbusdc
def buy_long_bnbusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='BNB/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_bnbusdc_swap_grid_long_buy_01',
                                per_qty=0.05,
                                take_profit_rate=0.02)
    strategy.max_order = 10
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.002
    strategy.min_profit_rate = 0.005
    # strategy.upper_limit = 841
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

# long buy ethusdc
def buy_long_ethusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='ETH/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_ethusdc_swap_grid_long_buy_01',
                                per_qty=0.02,
                                take_profit_rate=0.015)
    strategy.max_order = 10
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.002
    strategy.min_profit_rate = 0.005
    strategy.signal = AlphaTrendGridsSignal(AlphaTrendSignal(strategy.master_side.value))
    return strategy

# long buy xrpusdc
def buy_long_xrpusdc():
    strategy = SwapGridStrategy(ex_client=binance_client,
                                symbol='XRP/USDC',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='binance_xrpusdc_swap_grid_long_buy_01',
                                per_qty=20,
                                take_profit_rate=0.02)
    strategy.max_order = 10
    strategy.enable_exit_signal = True
    strategy.grid_gap_rate = 0.002
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
