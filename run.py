import json
import log
import os
import re
from DataEventLoop import BinanceDataEventLoop, Task
from strategy.bidirectional_grid_rotation_strategy import BidirectionalGridRotationStrategy
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

    # 加载策略配置
    with open('strategies.json', 'r') as f:
        strategies_config = json.load(f)

    kline_subscribes = []
    data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

    for strategy_config in strategies_config['strategies']:
        symbol = Symbol(
            base=strategy_config['symbol']['base'],
            quote=strategy_config['symbol']['quote']
        )
        
        # 添加K线订阅
        kline_subscribes.append(symbol.binance_ws_sub_kline(strategy_config['timeframe']))

        if strategy_config['strategy_type'] == 'bidirectional_grid_rotation':
            config = strategy_config['config']
            
            strategy = BidirectionalGridRotationStrategy(
                long_strategy=SignalGridStrategy(SignalGridStrategyConfig(
                    symbol=symbol,
                    position_side='long',
                    master_side=OrderSide.BUY,
                    per_order_qty=config['per_order_qty'],
                    grid_spacing_rate=config['grid_spacing_rate'],
                    max_order=config['max_order'],
                    enable_fixed_profit_taking=config['enable_fixed_profit_taking'],
                    fixed_take_profit_rate=config['fixed_take_profit_rate'],
                    enable_exit_signal=config['enable_exit_signal'],
                    signal_min_take_profit_rate=config['signal_min_take_profit_rate'],
                    signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY.value)),
                    order_file_path=strategy_config['order_files']['long'],
                ), binance_client),
                short_strategy=SignalGridStrategy(SignalGridStrategyConfig(
                    symbol=symbol,
                    position_side='short',
                    master_side=OrderSide.SELL,
                    per_order_qty=config['per_order_qty'],
                    grid_spacing_rate=config['grid_spacing_rate'],
                    max_order=config['max_order'],
                    enable_fixed_profit_taking=config['enable_fixed_profit_taking'],
                    fixed_take_profit_rate=config['fixed_take_profit_rate'],
                    enable_exit_signal=config['enable_exit_signal'],
                    signal_min_take_profit_rate=config['signal_min_take_profit_rate'],
                    signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL.value)),
                    order_file_path=strategy_config['order_files']['short'],
                ), binance_client),
                config=config['rotation_config'],
            )
            
            data_event_loop.add_task(StrategyTask(strategy))
            logger.info(f"Added strategy for {symbol.base.upper()}{symbol.quote.upper()}")

    data_event_loop.start()

