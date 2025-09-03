from DataEventLoop import BinanceDataEventLoop
from bidirectional_grid_rotation_task import BidirectionalGridRotationTask
from client.binance_client import BinanceSwapClient
import log
import os

from model import OrderSide, Symbol
from run import StrategyTask
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal

logger = log.getLogger(__name__)

if __name__ == "__main__":
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    is_test = os.environ.get("BINANCE_IS_TEST") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
    else:
        logger.info(
            f"api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}"
        )

    binance_client = BinanceSwapClient(
        api_key=api_key,
        api_secret=api_secret,
        is_test=is_test,
    )

    bnbusdc = Symbol(base="bnb", quote="usdt")
    per_order_qty = 0.01

    data_event_loop = BinanceDataEventLoop(
        kline_subscribes=[
            bnbusdc.binance_ws_sub_kline("1m"),
        ]
    )
    data_event_loop.add_task(
        StrategyTask(
            BidirectionalGridRotationTask(
                long_strategy=SignalGridStrategy(
                    SignalGridStrategyConfig(
                        symbol=bnbusdc,
                        position_side="long",
                        master_side=OrderSide.BUY,
                        per_order_qty=per_order_qty,
                        grid_spacing_rate=0.001,
                        max_order=10,
                        enable_fixed_profit_taking=True,
                        fixed_take_profit_rate=0.01,
                        enable_exit_signal=True,
                        signal_min_take_profit_rate=0.002,
                        signal=AlphaTrendGridsSignal(
                            AlphaTrendSignal(OrderSide.BUY.value)
                        ),
                        order_file_path="data/grids_strategy_v2_long_buy.json",
                    ),
                    binance_client,
                ),
                short_strategy=SignalGridStrategy(
                    SignalGridStrategyConfig(
                        symbol=bnbusdc,
                        position_side="short",
                        master_side=OrderSide.SELL,
                        per_order_qty=per_order_qty,
                        grid_spacing_rate=0.001,
                        max_order=10,
                        enable_fixed_profit_taking=True,
                        fixed_take_profit_rate=0.01,
                        enable_exit_signal=True,
                        signal_min_take_profit_rate=0.002,
                        signal=AlphaTrendGridsSignal(
                            AlphaTrendSignal(OrderSide.SELL.value)
                        ),
                        order_file_path="data/grids_strategy_v2_short_sell.json",
                    ),
                    binance_client,
                ),
            )
        )
    )
    data_event_loop.start()
