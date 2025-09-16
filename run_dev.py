from DataEventLoop import BinanceDataEventLoop
from strategy.bidirectional_grid_rotation_strategy import BidirectionalGridRotationStrategy
from client.binance_client import BinanceSwapClient
import log
import os
import json

from model import OrderSide, Symbol
from run import StrategyTask
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal

logger = log.getLogger(__name__)

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
    with open('strategies_dev.json', 'r') as f:
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

            # 构建long策略配置
            long_config_data = {
                'symbol': symbol,
                'position_side': 'long',
                'master_side': OrderSide.BUY,
                'signal': AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
                'order_file_path': strategy_config['order_files']['long'],
                **config
            }

            # 构建short策略配置
            short_config_data = {
                'symbol': symbol,
                'position_side': 'short',
                'master_side': OrderSide.SELL,
                'signal': AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.SELL)),
                'order_file_path': strategy_config['order_files']['short'],
                **config
            }

            strategy = BidirectionalGridRotationStrategy(
                long_strategy=SignalGridStrategy(
                    SignalGridStrategyConfig(**long_config_data), binance_client
                ),
                short_strategy=SignalGridStrategy(
                    SignalGridStrategyConfig(**short_config_data), binance_client
                ),
                config=config['rotation_config'],
            )

            strategy.long_strategy.init_kline_nums = 300
            strategy.short_strategy.init_kline_nums = 300
            strategy.rotation_increment = 1
            
            data_event_loop.add_task(StrategyTask(strategy))
            logger.info(f"Added strategy for {symbol.base.upper()}{symbol.quote.upper()}")

    data_event_loop.start()

