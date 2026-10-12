"""AlphaTrendSignal 单元测试

全部使用**确定性收盘价序列**（`make_klines_df`），不使用随机数、print 或
吞异常的 try/except。每个断言都验证具体语义，而非仅验证"返回值在范围内"。

序列构造（`period=8` 时翻转根固定落在 index 10 与 44）:
- `_DOWN_THEN_UP`: 先跌 40 根再涨 6 根 → index 10 卖出突破、index 44 **买入**突破
- `_UP_THEN_DOWN`: 先涨 40 根再跌 6 根 → index 10 买入突破、index 44 **卖出**突破

命名以**最后一根（翻转根）的方向**为准:
- `BREAKOUT_UP`: `_DOWN_THEN_UP[:45]`, 最后一根是买入突破
- `BREAKOUT_SHORT`: `_UP_THEN_DOWN[:45]`, 最后一根是卖出突破
- `FIRST_BREAKOUT_SHORT`: `_DOWN_THEN_UP[:11]`, 最后一根是首个卖出突破

注意: 突破只在**方向翻转的那一根**产出，因此测试序列必须**截断到翻转根**,
否则最后一根不是翻转根, 结果为中性。
"""

import pandas as pd
import pytest

from smart_trader.model import SignalDirection
from smart_trader.strategy.alpha_trend_signal.alpha_trend_indicator_v2 import (
    alpha_trend_indicator,
)
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from tests.fakes import make_klines_df

# 先跌 40 根, 再涨 6 根
_DOWN_THEN_UP = [100.0 - index * 0.5 for index in range(40)] + [
    80.0 + index * 0.9 for index in range(6)
]
# 先涨 40 根, 再跌 6 根
_UP_THEN_DOWN = [80.0 + index * 0.9 for index in range(40)] + [
    116.0 - index * 0.5 for index in range(6)
]
# 单调下跌: 方向从未翻转
DOWN_ONLY = [100.0 - index * 0.5 for index in range(45)]

# 截断到翻转根, 使 index 44 成为最后一根
BREAKOUT_UP = _DOWN_THEN_UP[:45]  # 最后一根 = 买入突破
BREAKOUT_SHORT = _UP_THEN_DOWN[:45]  # 最后一根 = 卖出突破
FIRST_BREAKOUT_SHORT = _DOWN_THEN_UP[:11]  # 首个卖出突破（index 10）


# ---- 构造 ----


def test_initialization_defaults():
    """默认参数: atr_multiple=1.0, period=8, 状态字段清零"""
    signal = AlphaTrendSignal()

    assert signal.atr_multiple == 1.0, "默认 atr_multiple 应为 1.0"
    assert signal.period == 8, "默认 period 应为 8"
    assert signal.datetime is None, "初始未处理任何 K 线"
    assert signal.current_signal == 0, "初始无方向"
    assert signal.current_trend == 0, "初始无趋势计数"


def test_initialization_accepts_custom_parameters():
    """自定义指标参数生效"""
    signal = AlphaTrendSignal(atr_multiple=2.0, period=14)

    assert signal.atr_multiple == 2.0
    assert signal.period == 14


def test_indicator_does_not_mutate_input_dataframe():
    """指标只读入参: 指标列必须加在副本上（回归）

    指标列曾直接写进入参 → 污染策略共享的 klines DataFrame（is_breakout /
    long_entry 等是 bool 列）→ `_update_klines` 行赋值时这些列被 NaN 补全 →
    pandas FutureWarning "Value 'nan' has dtype incompatible with bool"。
    """
    df = make_klines_df(BREAKOUT_UP)
    before_columns = list(df.columns)

    result = alpha_trend_indicator(df, period=8)

    assert list(df.columns) == before_columns, (
        f"入参 df 不得被添加指标列, 实际 {list(df.columns)}"
    )
    assert result is not df, "必须返回副本, 而非入参本身"
    for column in ("alpha_value", "trend", "is_breakout"):
        assert column in result.columns, f"副本应含指标列 {column}"


# ---- 空值与不足数据 ----


def test_evaluate_returns_neutral_when_history_shorter_than_period():
    """K 线数不足 period 根时返回中性（warmup 区域）"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df([100.0, 101.0, 102.0]))

    assert result.direction is SignalDirection.NEUTRAL, (
        "warmup 区域无趋势计数, 应返回中性"
    )


def test_evaluate_returns_neutral_on_empty_dataframe():
    """空 DataFrame 返回中性, 不抛 IndexError"""
    signal = AlphaTrendSignal(period=8)
    empty = pd.DataFrame(
        columns=["datetime", "open", "high", "low", "close", "volume", "finished"]
    )

    result = signal.evaluate(empty)

    assert result.direction is SignalDirection.NEUTRAL, "空数据应返回中性"


def test_evaluate_returns_neutral_on_single_row():
    """单行数据返回中性, 不抛异常"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df([100.0]))

    assert result.direction is SignalDirection.NEUTRAL, "单行数据不足以计算指标"


def test_evaluate_returns_neutral_on_flat_prices():
    """价格无波动时无趋势, 返回中性"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df([100.0] * 45))

    assert result.direction is SignalDirection.NEUTRAL, "横盘不应产生突破信号"


# ---- 方向 ----


def test_evaluate_returns_short_at_downward_breakout():
    """下跌趋势的翻转根产出看跌方向"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df(BREAKOUT_SHORT))

    assert result.direction is SignalDirection.SHORT, "下跌突破应给出看跌方向"
    assert signal.current_signal == -1, "内部方向状态应为 -1"


def test_evaluate_returns_long_at_upward_breakout():
    """上涨趋势的翻转根产出看涨方向"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df(BREAKOUT_UP))

    assert result.direction is SignalDirection.LONG, "上涨突破应给出看涨方向"
    assert signal.current_signal == 1, "内部方向状态应为 1"


def test_evaluate_carries_kline_datetime():
    """信号携带触发它的 K 线时间"""
    signal = AlphaTrendSignal(period=8)
    klines = make_klines_df(BREAKOUT_SHORT)

    result = signal.evaluate(klines)

    assert result.datetime == str(klines["datetime"].iloc[-1]), (
        "信号时间应等于最后一根 K 线的时间"
    )


def test_evaluate_emits_again_after_direction_flips_back():
    """方向翻转后再次产出信号（先跌到 index10 看跌, 再涨到 index44 翻转为看涨）"""
    signal = AlphaTrendSignal(period=8)
    signal.evaluate(make_klines_df(FIRST_BREAKOUT_SHORT))
    assert signal.current_signal == -1, "应先建立看跌状态"

    result = signal.evaluate(make_klines_df(BREAKOUT_UP))

    assert result.direction is SignalDirection.LONG, "方向翻转后应给出看涨"
    assert signal.current_signal == 1, "内部状态应同步更新为看涨"


# ---- 去重 ----


def test_evaluate_suppresses_same_direction_repeat():
    """同向连续突破只在首次翻转时产出, 其后为中性（去重）"""
    signal = AlphaTrendSignal(period=8)

    first = signal.evaluate(make_klines_df(BREAKOUT_SHORT))
    assert first.direction is SignalDirection.SHORT, "首次翻转应产出看跌"

    # 继续同向下跌: 方向未翻转, 被去重为中性
    second = signal.evaluate(make_klines_df(_DOWN_THEN_UP[:41]))

    assert second.direction is SignalDirection.NEUTRAL, "同向连续突破应被去重为中性"
    assert signal.current_signal == -1, "去重不应改变内部方向状态"


def test_evaluate_returns_neutral_on_monotonic_trend_tail():
    """单调趋势的尾部（非翻转根）返回中性"""
    signal = AlphaTrendSignal(period=8)

    result = signal.evaluate(make_klines_df(DOWN_ONLY))

    assert result.direction is SignalDirection.NEUTRAL, (
        "趋势未发生翻转时不应重复产出信号"
    )
    assert signal.current_trend < 0, "下跌趋势的计数应为负"


# ---- 幂等 ----


def test_evaluate_is_idempotent_within_same_kline():
    """幂等: 同一根 K 线重复求值返回相同结果"""
    signal = AlphaTrendSignal(period=8)
    klines = make_klines_df(BREAKOUT_SHORT)

    first = signal.evaluate(klines)
    second = signal.evaluate(klines)

    assert first.direction is second.direction, "同一根 K 线方向必须一致"
    assert first.datetime == second.datetime, "同一根 K 线时间必须一致"


def test_evaluate_reuses_cached_status_within_same_kline():
    """同一根 K 线重复求值复用缓存状态, 不重算指标"""
    signal = AlphaTrendSignal(period=8)
    klines = make_klines_df(BREAKOUT_SHORT)

    signal.evaluate(klines)
    cached = signal.current_kline_status

    signal.evaluate(klines)

    assert signal.current_kline_status == cached, "缓存命中时状态不应改变"


# ---- 无 first_run 反推（本次修复的缺陷）----


def test_evaluate_needs_no_state_seeding_from_history():
    """状态完全由 DataFrame 决定: 新实例直接求值即可产出突破信号

    旧实现需要 `first_run` 从历史反推 `current_signal`, 而反推取的是含当前
    K 线在内的最后一条信号, 导致进程重启后当前这根 K 线的突破被去重吞掉。
    v2 指标的趋势累积从序列开头算起, 不存在这个问题。
    """
    fresh = AlphaTrendSignal(period=8)

    result = fresh.evaluate(make_klines_df(BREAKOUT_SHORT))

    assert result.direction is SignalDirection.SHORT, (
        "全新实例应直接产出突破信号, 无需任何历史反推"
    )
    assert fresh.current_signal == -1, "内部方向应立即建立"


def test_two_fresh_instances_agree():
    """同一序列两个新实例求值结果一致（结果只取决于数据, 不取决于历史调用）"""
    closes = BREAKOUT_SHORT

    first = AlphaTrendSignal(period=8).evaluate(make_klines_df(closes))
    second = AlphaTrendSignal(period=8).evaluate(make_klines_df(closes))

    assert first.direction is second.direction, "同数据同结果"
    assert first.datetime == second.datetime


def test_evaluate_does_not_mutate_kline_index():
    """求值不改变 K 线 DataFrame 的行索引（信号是旁路分析）"""
    signal = AlphaTrendSignal(period=8)
    klines = make_klines_df(BREAKOUT_SHORT)
    before = list(klines.index)

    signal.evaluate(klines)

    assert list(klines.index) == before, "索引不应被信号改写"


def test_evaluate_on_new_kline_updates_datetime():
    """K 线推进时内部时间随之更新"""
    signal = AlphaTrendSignal(period=8)

    signal.evaluate(make_klines_df(_DOWN_THEN_UP[:44]))
    first_datetime = signal.datetime

    signal.evaluate(make_klines_df(_DOWN_THEN_UP[:45]))

    assert signal.datetime != first_datetime, "K 线推进后内部时间必须更新"


# ---- 隔离 ----


def test_two_signals_do_not_share_state():
    """隔离: 两个信号实例的状态互不影响"""
    first = AlphaTrendSignal(period=8)
    second = AlphaTrendSignal(period=8)

    first.evaluate(make_klines_df(BREAKOUT_SHORT))

    assert second.current_signal == 0, "未求值的实例方向应保持清零"
    assert second.datetime is None, "未求值的实例时间应保持 None"


def test_signal_is_a_signal_subclass():
    """实现须继承 Signal ABC（否则抽象方法失效）"""
    from smart_trader.strategy.signal import Signal

    assert issubclass(AlphaTrendSignal, Signal), "必须是 Signal 的子类"


def test_signal_cannot_be_instantiated_without_evaluate():
    """Signal 是真 ABC: 未实现 evaluate 的子类不可实例化"""
    from smart_trader.strategy.signal import Signal

    class Incomplete(Signal):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # pyright: ignore[reportAbstractUsage]