"""反转信号: 组合式包装器, 只反转方向, 其余字段原样透传

为什么反转发生在信号层: 入场闸门与离场策略共享同一个 `MarketSignal`,
在信号层反转才能让"多头信号 → 开空 / 空头信号 → 开多"对两侧同时生效,
不会出现入场反向、出场未反向的半吊子语义。

`NEUTRAL` 没有反向可言, 经 `SignalDirection.opposite()` 保持中性。
"""

from pandas import DataFrame

from smart_trader.model import MarketSignal
from smart_trader.strategy.signal import Signal


class InvertedSignal(Signal):
    """反转内层信号的方向

    @param inner 被包装的信号; 其状态与去重语义保持不变
    """

    def __init__(self, inner: Signal) -> None:
        self._inner = inner

    @property
    def inner(self) -> Signal:
        """被包装的信号（只读; 配置序列化据此提取指标参数）"""
        return self._inner

    def evaluate(self, klines: DataFrame) -> MarketSignal:
        """求值并反转方向; `datetime` 原样透传"""
        signal = self._inner.evaluate(klines)
        return signal.model_copy(
            update={"direction": signal.direction.opposite()}
        )
