from abc import ABC, abstractmethod
from typing import Any

class StrategyRepository(ABC):
    @abstractmethod
    def save_strategy_instance(self, strategy_id: str, strategy_type: str, symbol: str, config_data: str) -> None:
        """保存策略实例配置"""
        pass

    @abstractmethod
    def save_active_orders(self, strategy_id: str, orders: list[dict[str, Any]]) -> None:
        """保存当前活跃的网格订单，全量覆盖或基于 ID 更新"""
        pass

    @abstractmethod
    def load_active_orders(self, strategy_id: str) -> list[dict[str, Any]]:
        """加载当前活跃的网格订单"""
        pass

    @abstractmethod
    def append_trade_history(self, strategy_id: str, trade_record: dict[str, Any]) -> None:
        """追加一条历史交易利润记录"""
        pass

    @abstractmethod
    def load_trade_history(self, strategy_id: str) -> list[dict[str, Any]]:
        """加载历史交易记录"""
        pass
