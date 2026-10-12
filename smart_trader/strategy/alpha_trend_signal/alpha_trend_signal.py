from pandas import DataFrame

from smart_trader.model import MarketSignal, SignalDirection
from smart_trader.strategy.alpha_trend_signal.alpha_trend_indicator_v2 import (
    alpha_trend_indicator,
)
from smart_trader.strategy.signal import Signal

_datetime = "datetime"
_trend = "trend"
_is_breakout = "is_breakout"

# 突破方向: 趋势计数为正即买入突破, 为负即卖出突破
_DIRECTION_BY_SIGN = {1: SignalDirection.LONG, -1: SignalDirection.SHORT}


class AlphaTrendSignal(Signal):
    """AlphaTrend 趋势突破信号

    采用 v2 指标（`alpha_trend_indicator_v2.py`）的趋势累积计数口径:
    `trend` 由序列开头逐根累积, 方向切换的那一根被标记为突破根。
    信号因此**完全由 DataFrame 决定, 无需从历史反推初始状态** ——
    这消除了旧实现 `first_run` 反推（反推结果含当前 K 线）导致的重启后漏信号问题。

    同向连续突破只在**方向翻转**时产出信号（`current_signal` 去重）,
    其后同根 K 线均返回中性。状态留内存, 不落库。
    """

    def __init__(self, atr_multiple: float = 1.0, period: int = 8):
        self.atr_multiple = atr_multiple
        self.period = period
        self.datetime: str | None = None
        self.current_signal: int = 0
        self.current_kline_status: int = 0
        self.current_trend: int = 0

    def evaluate(self, klines: DataFrame) -> MarketSignal:
        """求值: 同一根 K 线重复调用返回相同结果（幂等）

        @param klines OHLCV DataFrame, 由策略提供; 实现不得替换、清空或增删列
        @return 翻转突破根返回对应方向, 其余为中性
        @note 空数据返回中性, 不抛异常
        """
        if klines.empty:
            return MarketSignal()

        last_time = str(klines[_datetime].iloc[-1])
        if self.datetime == last_time:
            return self._signal_of(self.current_kline_status, last_time)

        trend, is_breakout = self._last_trend(klines)
        self.current_trend = trend

        signal_value = 0
        if is_breakout:
            candidate = 1 if trend > 0 else -1
            if self.current_signal != candidate:
                self.current_signal = candidate
                signal_value = candidate

        self.current_kline_status = signal_value
        self.datetime = last_time
        return self._signal_of(signal_value, last_time)

    def _last_trend(self, klines: DataFrame) -> tuple[int, bool]:
        """最后一根 K 线的 (趋势计数, 是否为方向翻转的突破根)

        @note 走 v2 指标的公开入口; 该函数就地往 DataFrame 追加指标列
        """
        df = alpha_trend_indicator(klines, self.atr_multiple, self.period)
        if df.empty:
            return 0, False
        return int(df[_trend].iloc[-1]), bool(df[_is_breakout].iloc[-1])

    @staticmethod
    def _signal_of(value: int, datetime: str) -> MarketSignal:
        return MarketSignal(
            direction=_DIRECTION_BY_SIGN.get(value, SignalDirection.NEUTRAL),
            datetime=datetime,
        )