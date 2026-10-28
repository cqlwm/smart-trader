"""策略控制面契约（P1）

`CommandRunner` 只依赖本 ABC, 不直接触碰 `SignalGridStrategy` 内部字段;
控制动作全部由策略自己实现, 保证账本写入仍走既有 Gateway / PositionLifecycle。
"""

from abc import ABC, abstractmethod

from smart_trader.model import StrategyRuntime


class ControllableStrategy(ABC):
    """可被外部命令控制的策略"""

    @property
    @abstractmethod
    def strategy_key(self) -> str:
        """策略稳定标识（commands.strategy_key 的匹配键）"""

    @abstractmethod
    def runtime_info(self) -> StrategyRuntime:
        """运行态快照（心跳文件用, 不落账本）"""

    @abstractmethod
    def pause(self) -> None:
        """暂停新的入场 / 离场决策（在途出场单仍跟踪, 持仓对账仍执行）"""

    @abstractmethod
    def resume(self) -> None:
        """恢复决策"""

    @abstractmethod
    def request_close_all(self) -> None:
        """请求全平: 置一次性边沿标志, 下一根完成 K 线消费"""

    @abstractmethod
    def cancel_pending_entries(self) -> tuple[int, int]:
        """撤销全部未成交入场单

        @return: (涉及仓位数, 归零且无存活订单而归档的仓位数)
        """
