from datetime import UTC, datetime

from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.config import DATA_PATH
from smart_trader.model import OrderSide, PositionSide, Symbol
from smart_trader.strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol_=Symbol(base="xau", quote="usdt")
timeframe_= '1m'
today_utc = datetime.now(UTC).strftime("%Y%m%d")

def long_buy(exchange_client: ExSwapClient) -> StrategyTask:
    config=SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=1,
        grid_spacing_rate=-0.1,
        max_order=3,
        enable_exit_signal=True,
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
        exit_signal_take_profit_min_rate=0.03,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.03,
        order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol_.simple()}_{timeframe_}.json',
    )
    strategy = SignalGridStrategy(config, exchange_client)

    return StrategyTask(symbol=symbol_, strategy=strategy)
