import pandas as pd
from pandas import DataFrame

from model import OrderSide
from strategies.smc.models import SMCConfig
from strategies.smc.engine import SMCEngine
from strategies.smc.signal import compute_signal
from strategy import Signal


class SMCSignal(Signal):
    """单周期 SMC 信号，将 SMCEngine 分析结果转为 +1/-1/0。

    使用方式与 AlphaTrendSignal 一致，可传给 SignalGridStrategy 等策略。
    """

    def __init__(self, side: OrderSide, smc_config: SMCConfig | None = None):
        super().__init__(side)
        self.smc_config = smc_config or SMCConfig()
        self._engine = SMCEngine(self.smc_config)
        self._last_signal: int = 0

    def run(self, klines: DataFrame) -> int:
        closed = klines[klines["finished"] == True].copy()
        closed["datetime"] = pd.to_datetime(closed["datetime"], utc=True)
        smc_df = closed[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        if len(smc_df) < 50:
            return 0

        result = self._engine.analyze(smc_df)
        signal_result = compute_signal(result)

        if signal_result.signal.action == "LONG":
            new_signal = 1
        elif signal_result.signal.action == "SHORT":
            new_signal = -1
        else:
            self._last_signal = 0
            return 0

        if new_signal == self._last_signal:
            return 0

        self._last_signal = new_signal
        return new_signal
