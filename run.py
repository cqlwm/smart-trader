from datetime import datetime
import json
import re
from DataEventLoop import BinanceDataEventLoop, Task
from client.binance_client import BinanceSwapClient
from model import Symbol, Kline
from strategy import StrategyV2
from strategy.grids_strategy_v2 import SignalGridStrategyConfig, SignalGridStrategy

class SimpleStrategyV2(StrategyV2):
    def __init__(self):
        super().__init__()

    def on_kline_finished(self):
        print("####👇")
        print(self.klines)
        print("####👆")

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
    btcusdt = Symbol(base='btc', quote='usdt')
    data_event_loop = BinanceDataEventLoop(kline_subscribes=[
        btcusdt.binance_ws_sub_kline('1m'), 
    ])
    binance_client = BinanceSwapClient(
        api_key="777669c469d163669b3cb2d7a4585d3b96ae43dca184692089d5dbdfebe960b5",
        api_secret="32288d111051f3ba22be9be6428eea14b570524311831087e0b34a2fe819dd1c",
        is_test=True,
    )
    data_event_loop.add_task(StrategyTask(SignalGridStrategy(SignalGridStrategyConfig(
        symbol=btcusdt,
        per_order_qty=0.001,
        grid_spacing_rate=0.0001,
        enable_fixed_profit_taking=True,
        fixed_take_profit_rate=0.0001,
    ), binance_client)))
    data_event_loop.start()
