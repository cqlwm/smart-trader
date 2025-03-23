from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_signal_v3 import AlphaTrendSignalV3
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal

from client.okx_client import OkxSwapClient
from strategy.grids_strategy import SwapGridStrategy
from strategy import OrderSide
from klines import KlineStream

okx_client = OkxSwapClient(
    api_key="cbfa5230-b8bb-4299-87e1-62fbb0652bd0",
    secret="493756B7F504AB881AF43CC328D06175",
    password="uc30ZNLfhvjeW.",
    test=False
)


def execution(ks: KlineStream):
    # ks.subscribes(key='solusdt@kline_5m', strategies=[sol_grid_long_buy()])
    ks.subscribes(key='penguusdt@kline_5m', strategies=[pengu_grid_long_buy()])

def sol_grid_long_buy():
    strategy = SwapGridStrategy(ex_client=okx_client,
                                symbol='SOL-USDT-SWAP',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='okx_solusdt_swap_grid_long_buy_01',
                                per_qty=0.1,
                                take_profit_rate=0.1)
    strategy.max_order = 20
    strategy.grid_gap_rate = 0.003
    strategy.min_profit_rate = 0.003
    strategy.enable_exit_signal = True
    strategy.signal = AlphaTrendSignal(strategy.master_side.value)
    return strategy

def pengu_grid_long_buy():
    strategy = SwapGridStrategy(ex_client=okx_client,
                                symbol='PENGU-USDT-SWAP',
                                position_side='long',
                                master_side=OrderSide.BUY,
                                strategy_key='okx_penguusdt_swap_grid_long_buy_01',
                                per_qty=3,
                                take_profit_rate=0.05)
    strategy.upper_limit = 0.039044
    strategy.grid_gap_rate = 0.001
    strategy.min_profit_rate = 0.001
    strategy.enable_exit_signal = True
    strategy.signal = AlphaTrendSignal(strategy.master_side.value)
    return strategy


def _test():
    print('run okx_swap_grid_long_buy_solusdt_01')

    global okx_client
    okx_client = OkxSwapClient(
        api_key="90b3ca95-27b9-4786-aca1-e73e5f3112f8",
        secret="1DB945126584273D4C5E7843FD2764D7",
        password="147258azS.",
        test=True)

    ks = KlineStream()
    execution(ks)
    ks.start()


if __name__ == '__main__':
    _test()
