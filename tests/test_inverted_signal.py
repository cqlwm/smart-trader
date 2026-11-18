"""InvertedSignal 单元测试: 只反转非中性方向, 其余语义原样透传

方向反转发生在信号层, 入场闸门与离场策略共享同一个 `MarketSignal`,
因此两侧同时反向; NEUTRAL 保持不变, 不得被反转成任何方向。

AlphaTrend 序列复用 `test_alpha_trend_signal_unit.py` 的确定性构造:
- `BREAKOUT_UP` 最后一根为买入突破（原始 LONG）
- `BREAKOUT_SHORT` 最后一根为卖出突破（原始 SHORT）
"""

from pandas import DataFrame

from smart_trader.model import MarketSignal, SignalDirection
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import (
    AlphaTrendSignal,
)
from smart_trader.strategy.inverted_signal import InvertedSignal
from smart_trader.strategy.signal import Signal
from tests.fakes import make_klines_df

_DOWN_THEN_UP = [100.0 - index * 0.5 for index in range(40)] + [
    80.0 + index * 0.9 for index in range(6)
]
_UP_THEN_DOWN = [80.0 + index * 0.9 for index in range(40)] + [
    116.0 - index * 0.5 for index in range(6)
]

BREAKOUT_UP = _DOWN_THEN_UP[:45]  # 原始信号 LONG
BREAKOUT_SHORT = _UP_THEN_DOWN[:45]  # 原始信号 SHORT


class _StaticSignal(Signal):
    """固定返回一个 MarketSignal, 用于隔离反转逻辑本身"""

    def __init__(self, signal: MarketSignal) -> None:
        self._signal = signal

    def evaluate(self, klines: DataFrame) -> MarketSignal:
        return self._signal


def test_inverted_signal_is_a_signal_subclass():
    """必须继承 Signal ABC"""
    assert issubclass(InvertedSignal, Signal), "必须是 Signal 的子类"


def test_inverts_long_to_short():
    """内层 LONG → 输出 SHORT"""
    inner = _StaticSignal(
        MarketSignal(direction=SignalDirection.LONG, datetime="2026-01-01 00:00:00")
    )

    result = InvertedSignal(inner).evaluate(make_klines_df([100.0]))

    assert result.direction is SignalDirection.SHORT, (
        f"LONG 应反转为 SHORT, 实际 {result.direction}"
    )
    assert result.datetime == "2026-01-01 00:00:00", "K 线时间必须原样保留"


def test_inverts_short_to_long():
    """内层 SHORT → 输出 LONG"""
    inner = _StaticSignal(
        MarketSignal(direction=SignalDirection.SHORT, datetime="2026-01-01 00:01:00")
    )

    result = InvertedSignal(inner).evaluate(make_klines_df([100.0]))

    assert result.direction is SignalDirection.LONG, (
        f"SHORT 应反转为 LONG, 实际 {result.direction}"
    )
    assert result.datetime == "2026-01-01 00:01:00", "K 线时间必须原样保留"


def test_keeps_neutral_unchanged():
    """NEUTRAL 没有反向可言, 必须保持中性"""
    inner = _StaticSignal(MarketSignal(datetime="2026-01-01 00:02:00"))

    result = InvertedSignal(inner).evaluate(make_klines_df([100.0]))

    assert result.direction is SignalDirection.NEUTRAL, (
        f"NEUTRAL 不应被反转, 实际 {result.direction}"
    )
    assert result.datetime == "2026-01-01 00:02:00", "空信号的时间也要透传"


def test_inverts_alpha_trend_upward_breakout():
    """包裹 AlphaTrendSignal: 原始看涨突破 → 输出看跌"""
    klines = make_klines_df(BREAKOUT_UP)
    raw = AlphaTrendSignal(period=8).evaluate(klines)
    inverted = InvertedSignal(AlphaTrendSignal(period=8)).evaluate(klines)

    assert raw.direction is SignalDirection.LONG, "前置条件: 原始信号应为 LONG"
    assert inverted.direction is SignalDirection.SHORT, (
        f"反转后应为 SHORT, 实际 {inverted.direction}"
    )
    assert inverted.datetime == raw.datetime, "反转不得改变信号时间"


def test_inverts_alpha_trend_downward_breakout():
    """包裹 AlphaTrendSignal: 原始看跌突破 → 输出看涨"""
    klines = make_klines_df(BREAKOUT_SHORT)
    raw = AlphaTrendSignal(period=8).evaluate(klines)
    inverted = InvertedSignal(AlphaTrendSignal(period=8)).evaluate(klines)

    assert raw.direction is SignalDirection.SHORT, "前置条件: 原始信号应为 SHORT"
    assert inverted.direction is SignalDirection.LONG, (
        f"反转后应为 LONG, 实际 {inverted.direction}"
    )


def test_evaluate_is_idempotent_within_same_kline():
    """同一根 K 线重复求值返回相同结果"""
    signal = InvertedSignal(AlphaTrendSignal(period=8))
    klines = make_klines_df(BREAKOUT_SHORT)

    first = signal.evaluate(klines)
    second = signal.evaluate(klines)

    assert first.direction is second.direction, "同一根 K 线方向必须一致"
    assert first.datetime == second.datetime, "同一根 K 线时间必须一致"


def test_preserves_inner_dedup_semantics():
    """内层去重保持不变: 非翻转根反转后仍是 NEUTRAL"""
    signal = InvertedSignal(AlphaTrendSignal(period=8))

    first = signal.evaluate(make_klines_df(BREAKOUT_SHORT))
    assert first.direction is SignalDirection.LONG, "原始看跌突破应反转为看涨"

    # 继续同向下跌: 原始信号被去重为 NEUTRAL, 反转后仍为 NEUTRAL
    second = signal.evaluate(make_klines_df(_UP_THEN_DOWN[:41]))

    assert second.direction is SignalDirection.NEUTRAL, (
        f"去重后的中性信号不应被反转, 实际 {second.direction}"
    )


def test_exposes_wrapped_signal():
    """内层信号可读取, 供配置序列化提取指标参数"""
    inner = AlphaTrendSignal(atr_multiple=2.0, period=14)

    assert InvertedSignal(inner).inner is inner, "inner 必须返回被包裹的实例"


def test_two_inverted_signals_do_not_share_state():
    """隔离: 两个反转实例的状态互不影响"""
    first = InvertedSignal(AlphaTrendSignal(period=8))
    second = InvertedSignal(AlphaTrendSignal(period=8))

    first.evaluate(make_klines_df(BREAKOUT_SHORT))

    assert second.evaluate(make_klines_df(_UP_THEN_DOWN[:41])).direction is (
        SignalDirection.NEUTRAL
    ), "未求值的实例不应受另一个实例影响"
