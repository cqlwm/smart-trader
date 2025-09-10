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

            strategy.long_strategy.init_kline_nums = 1
            strategy.short_strategy.init_kline_nums = 1
            strategy.rotation_increment = 1
            
            data_event_loop.add_task(StrategyTask(strategy))
            logger.info(f"Added strategy for {symbol.base.upper()}{symbol.quote.upper()}")

    data_event_loop.start()

