"""dogeusdt 顺势加仓做多模板的装配契约

模板是"配置即代码": 本文件锁定关键装配语义（方向 / 信号口径 / 顺势间距 /
账本键）, 防止示例配置被误改回网格口径或反向。
模板自身的参数数值（间距 / 仓位 / 止盈止损率）不在此锁定。
"""

from smart_trader.exchange.ports import Exchange
from smart_trader.model import OrderSide, Position, PositionSide, SignalKind, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from smart_trader.task.strategy_task import StrategyTask
from template import dogeusdt_trend_long
from tests.fakes import FakeMarketDataPort, FakeTradingPort


class _FakeExchange(FakeTradingPort, FakeMarketDataPort, Exchange):
    """组合内存端口的最小 Exchange 实现（模板工厂要求完整交易所能力）"""

    def __init__(self) -> None:
        FakeTradingPort.__init__(self)
        FakeMarketDataPort.__init__(self)

    def balance(self, coin: str) -> float:
        return 0.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        return []


def _build() -> tuple[SignalGridStrategy, StrategyTask]:
    task = dogeusdt_trend_long.trend_long(_FakeExchange(), OrderJournal(":memory:"))
    strategy = task.strategy
    assert isinstance(strategy, SignalGridStrategy), (
        f"模板必须装配 SignalGridStrategy, 实际 {type(strategy).__name__}"
    )
    return strategy, task


def test_template_is_a_long_buy_strategy():
    """方向契约: 做多 —— 买单入场, 不依赖反向开关"""
    strategy, _ = _build()

    assert strategy.config.position_side is PositionSide.LONG, "模板必须是多头"
    assert strategy.config.master_side is OrderSide.BUY, "入场方向必须是买入"


def test_template_uses_directional_alpha_trend_signal():
    """信号契约: AlphaTrend 信号 + DIRECTIONAL（只许看涨方向加仓）"""
    strategy, _ = _build()

    assert isinstance(strategy.config.signal, AlphaTrendSignal), (
        "模板必须使用 AlphaTrendSignal"
    )
    assert strategy.config.signal_kind is SignalKind.DIRECTIONAL, (
        "顺势加仓必须按信号方向许可入场, 不能用无方向倾向的 TRIGGER"
    )


def test_template_assembles_trend_spacing_gate():
    """加仓契约: 顺势间距闸门替换网格间距闸门（顺势阶梯, 互斥装配）"""
    strategy, _ = _build()

    gate_names = [type(gate).__name__ for gate in strategy.entry_gates.gates]

    assert "TrendSpacingGate" in gate_names, "顺势模板必须装配顺势间距闸门"
    assert "GridSpacingGate" not in gate_names, (
        "顺势与网格间距口径互斥, 不得同时装配"
    )
    assert strategy.config.trend_spacing_rate is not None, (
        "trend_spacing_rate 必须显式声明, 否则回退为网格口径"
    )


def test_template_has_own_ledger_key():
    """账本契约: 策略键独立于既有 DOGE 短网格（防串账本）"""
    strategy, _ = _build()

    assert strategy.config.strategy_key == "signal_trend_long_buy_dogeusdt_1m", (
        f"账本键被修改, 实际 {strategy.config.strategy_key}"
    )


def test_template_builds_doge_usdt_task():
    """任务契约: 订阅 DOGE/USDT 1m"""
    strategy, task = _build()

    assert task.symbol.binance() == "DOGEUSDT"
    assert strategy.config.symbol == task.symbol
    assert strategy.config.timeframe == "1m"