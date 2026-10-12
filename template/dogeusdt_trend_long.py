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

symbol_ = Symbol(base="doge", quote="usdt")
timeframe_ = "1m"


def trend_long(exchange: Exchange, journal: OrderJournal) -> StrategyTask:
    """AlphaTrend 顺势加仓做多

    - 入场: `signal_kind=DIRECTIONAL` 只许可看涨信号; 叠加 `trend_spacing_rate`
      顺势阶梯 —— 价格相对**最外层**仓位再顺向走出 0.5% 才加一层, 亏损方向不加仓;
    - 出场: 固定比例止盈（单层 1%）+ 信号反向止盈（单层 0.3%）+ 单层止损（5%）;
    - 规模: 金字塔最多 `max_order` 层, 每层 `per_order_qty`。

    @note 加仓节奏受信号语义约束: `AlphaTrendSignal` 是翻转事件信号,
          每次看涨翻转最多加一层; 若需"趋势延续中每隔 X% 加一层",
          需换成状态型信号（趋势持续期间恒输出方向）。
    @note 需要"逆势逐层减仓"时, 追加 `enable_signal_stop_loss=True`
          （信号反向时每根完成 K 线只减最外层一层, 亏损也减）。
    """
    position_side = PositionSide.LONG
    master_side = OrderSide.BUY

    config = SignalGridStrategyConfig(
        symbol=symbol_,
        timeframe=timeframe_,
        position_side=position_side,
        master_side=master_side,
        per_order_qty=500,
        # 顺势加仓间距: 越过最外层 0.01% 才加一层（顺势阶梯, 替代网格间距口径）
        trend_spacing_rate=0.0001,
        max_order=10,

        signal=AlphaTrendSignal(),
        signal_kind=SignalKind.DIRECTIONAL,
        strategy_key=(
            f"signal_trend_{position_side.value}_{master_side.value}"
            f"_{symbol_.simple()}_{timeframe_}"
        ),

        # 信号止盈: 浮盈层遇反向信号即离场（当前关闭;
        # 信号止损已开启: 浮亏的最外层遇反向信号 force 减仓, 亏损也走）
        enable_signal_take_profit=False,
        enable_signal_stop_loss=True,

        # 固定比例止盈: 单层盈利 10% 落袋
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.1,

        # 单层止损: 价格维度保护
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.005,
    )
    strategy = SignalGridStrategy(
        config, trading=exchange, market=exchange, journal=journal
    )

    return StrategyTask(symbol=symbol_, strategy=strategy)