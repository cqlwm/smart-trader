from smart_trader import log
from smart_trader.client.binance_client import BinanceSwapClient
from smart_trader.model import OrderSide, PositionSide, SignalKind, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.signal_grid_strategy import (
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol_ = Symbol(base="doge", quote="usdt")
timeframe_ = "1m"


def grid(client: BinanceSwapClient, journal: OrderJournal) -> StrategyTask:
    # 空头网格: 以卖单入场、买单出场。原实现靠 position_reverse 反转
    # LONG/BUY 得到该效果, 现改为直接声明真实方向。
    position_side = PositionSide.SHORT
    master_side = OrderSide.SELL

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=position_side,
        master_side=master_side,
        per_order_qty=500,
        grid_spacing_rate=0.0001,
        max_order=50,
        enable_exit_signal=True,
        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.TRIGGER,
        exit_signal_take_profit_min_rate=0.002,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.005,
        strategy_key=(
            f"signal_grid_{position_side.value}_{master_side.value}"
            f"_{symbol_.simple()}_{timeframe_}"
        ),
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )
    strategy = SignalGridStrategy(
        config, trading=client, market=client, journal=journal
    )

    return StrategyTask(symbol=symbol_, strategy=strategy)