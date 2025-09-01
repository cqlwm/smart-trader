from datetime import datetime
import json
from DataEventLoop import BinanceDataEventLoop, Task
from model import Symbol, Kline
from strategy import StrategyV2

class SimpleStrategyV2(StrategyV2):
    def __init__(self):
        super().__init__()

    def on_kline_finished(self, kline: Kline):
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
            kline = Kline(
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
    data_event_loop.add_task(StrategyTask(SimpleStrategyV2()))
    data_event_loop.start()
