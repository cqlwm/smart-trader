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

symbol_ = Symbol(base="zec", quote="usdc")
timeframe_ = "1m"
qty_ = 0.04

def grid_long(exchange: Exchange, journal: OrderJournal) -> StrategyTask:
    position_side = PositionSide.LONG
    master_side = OrderSide.BUY

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=position_side,
        master_side=master_side,
        per_order_qty=qty_,
        # 顺势加仓间距: 越过最外层 0.01% 才加一层（顺势阶梯, 替代网格间距口径）
        grid_spacing_rate=0.0001,
        max_order=10,

        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.TRIGGER,
        strategy_key=f"signal_grid_{symbol_.simple()}_{timeframe_}_{position_side.value}_{master_side.value}",

        # 信号止盈: 浮盈层遇反向信号即离场（当前关闭;
        # 信号止损已开启: 浮亏的最外层遇反向信号 force 减仓, 亏损也走）
        enable_signal_take_profit=True,
        enable_signal_stop_loss=False,

        # 固定比例止盈: 单层盈利 10% 落袋
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,

        # 单层止损: 价格维度保护
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.1,

        enable_max_order_stop_loss=True,
    )
    strategy = SignalGridStrategy(config, trading=exchange, market=exchange, journal=journal, account=exchange)

    return StrategyTask(symbol=symbol_, strategy=strategy)

def trend_long(exchange: Exchange, journal: OrderJournal) -> StrategyTask:
    position_side = PositionSide.LONG
    master_side = OrderSide.BUY

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=position_side,
        master_side=master_side,
        per_order_qty=qty_,
        # 顺势加仓间距: 越过最外层 0.01% 才加一层（顺势阶梯, 替代网格间距口径）
        trend_spacing_rate=0.0001,
        max_order=50,

        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.TRIGGER,
        strategy_key=f"signal_trend_{symbol_.simple()}_{timeframe_}{position_side.value}_{master_side.value}",

        # 信号止盈: 浮盈层遇反向信号即离场（当前关闭;
        # 信号止损已开启: 浮亏的最外层遇反向信号 force 减仓, 亏损也走）
        enable_signal_take_profit=False,
        enable_signal_stop_loss=False,

        # 固定比例止盈: 单层盈利 10% 落袋
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.10,

        # 单层止损: 价格维度保护
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.005,
    )
    strategy = SignalGridStrategy(
        config, trading=exchange, market=exchange, journal=journal, account=exchange
    )

    return StrategyTask(symbol=symbol_, strategy=strategy)

def trend_short(exchange: Exchange, journal: OrderJournal) -> StrategyTask:
    position_side = PositionSide.SHORT
    master_side = OrderSide.SELL

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=position_side,
        master_side=master_side,
        per_order_qty=qty_,
        # 顺势加仓间距: 越过最外层 0.01% 才加一层（顺势阶梯, 替代网格间距口径）
        trend_spacing_rate=0.0001,
        max_order=50,

        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.TRIGGER,
        strategy_key=f"signal_trend_{symbol_.simple()}_{timeframe_}_{position_side.value}_{master_side.value}",

        # 信号止盈: 浮盈层遇反向信号即离场（当前关闭;
        # 信号止损已开启: 浮亏的最外层遇反向信号 force 减仓, 亏损也走）
        enable_signal_take_profit=False,
        enable_signal_stop_loss=True,

        # 固定比例止盈: 单层盈利 10% 落袋
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.10,

        # 单层止损: 价格维度保护
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.005,
    )
    strategy = SignalGridStrategy(config, trading=exchange, market=exchange, journal=journal, account=exchange)

    return StrategyTask(symbol=symbol_, strategy=strategy)