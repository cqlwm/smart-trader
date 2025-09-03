import json
import log
import os
import re
from DataEventLoop import BinanceDataEventLoop, Task
from bidirectional_grid_rotation_task import BidirectionalGridRotationTask
from client.binance_client import BinanceSwapClient
from model import Symbol, Kline
from strategy import StrategyV2
from model import OrderSide
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from strategy.grids_strategy_v2 import SignalGridStrategyConfig, SignalGridStrategy

logger = log.getLogger(__name__)

class StrategyTask(Task):
    def __init__(self, strategy: StrategyV2):
        super().__init__()
        self.name = 'StrategyTask'
        self.strategy = strategy

    def run(self, data: str):
        data_obj = json.loads(data)

        kline_key = data_obj.get('stream', '')
        is_kline = '@kline_' in kline_key
        kline = data_obj.get('data', {}).get('k', None)
        
        if is_kline and kline:
            match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', kline_key)
            if not match:
                raise ValueError(f'Invalid kline key: {kline_key}')
            kline = Kline(
                symbol=Symbol(base=match.group(1), quote=match.group(2)),
                timeframe=match.group(3),
                open=float(kline['o']),
                high=float(kline['h']),
                low=float(kline['l']),
                close=float(kline['c']),
                volume=float(kline['v']),
                timestamp=int(kline['t']),
                finished=kline.get('x', False)
            )
            self.strategy.run(kline)

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

    bnbusdc = Symbol(base='bnb', quote='usdt')
    per_order_qty = 0.01

    data_event_loop = BinanceDataEventLoop(kline_subscribes=[
        bnbusdc.binance_ws_sub_kline('1m'), 
    ])
    data_event_loop.add_task(StrategyTask(BidirectionalGridRotationTask(
        long_strategy=SignalGridStrategy(SignalGridStrategyConfig(
            symbol=bnbusdc,
            position_side='long',
            master_side=OrderSide.BUY,
            per_order_qty=per_order_qty,
            grid_spacing_rate=0.001,
            max_order=10,
            enable_fixed_profit_taking=True,
            fixed_take_profit_rate=0.01,
            enable_exit_signal=True,
            signal_min_take_profit_rate=0.002,
            signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY.value)),
            order_file_path='data/grids_strategy_v2_long_buy.json',
        ), binance_client),
        short_strategy=SignalGridStrategy(SignalGridStrategyConfig(
            symbol=bnbusdc,
            position_side='short',
            master_side=OrderSide.SELL,
            per_order_qty=per_order_qty,
            grid_spacing_rate=0.001,
            max_order=10,
            enable_fixed_profit_taking=True,
            fixed_take_profit_rate=0.01,
            enable_exit_signal=True,
            signal_min_take_profit_rate=0.002,
            signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL.value)),
            order_file_path='data/grids_strategy_v2_short_sell.json',
        ), binance_client),
    )))
    data_event_loop.start()