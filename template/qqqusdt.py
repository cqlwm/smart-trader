from smart_trader import log
from smart_trader.exchange.ports import Exchange
from smart_trader.model import OrderSide, PositionSide, SignalKind, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.signal_grid_strategy import (
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)

symbol_ = Symbol(base="xau", quote="usdt")
timeframe_ = "1m"


def long_buy(exchange: Exchange, journal: OrderJournal) -> StrategyTask:
    """多头网格

    `grid_spacing_rate` 为**负值**, 表示双向距离限制: 只要相对基准仓位的涨跌幅
    在 10% 以内就允许加仓 —— 顺势下跌加仓, 逆势上涨小幅加仓都会触发。
    正值才是"仅在亏损方向走出间距后加仓"。
    """
    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=PositionSide.LONG,
        master_side=OrderSide.BUY,
        per_order_qty=1,
        grid_spacing_rate=-0.1,
        max_order=3,
        enable_signal_take_profit=True,
        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.TRIGGER,
        exit_signal_take_profit_min_rate=0.03,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.03,
        strategy_key=f"signal_grid_long_buy_{symbol_.simple()}_{timeframe_}",
    )
    strategy = SignalGridStrategy(
        config, trading=exchange, market=exchange, journal=journal
    )

    return StrategyTask(symbol=symbol_, strategy=strategy)