"""行情信号分析器: 只回答"行情现在是什么状态"

信号**不含持仓语义**, 也不含入场/离场概念 —— 方向消费方是
`EntryGate` 与 `ExitPolicy`（见 `03-coding-design.md` 第三节）。
"""

from abc import ABC, abstractmethod

from pandas import DataFrame

from smart_trader.model import MarketSignal


class Signal(ABC):
    """行情信号分析器

    实现须保证幂等: 同一根 K 线重复求值必须返回相同结果
    （`AlphaTrendSignal` 以 `datetime` 做 K 线级缓存）。
    """

    @abstractmethod
    def evaluate(self, klines: DataFrame) -> MarketSignal:
        """对当前 K 线序列求值

        @param klines OHLCV DataFrame, 由策略提供, 实现不得替换或清空
        @return 本根 K 线的行情信号
        """