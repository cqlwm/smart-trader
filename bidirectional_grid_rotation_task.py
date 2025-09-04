import json
import os
from model import Kline
from strategy import StrategyV2
from strategy.grids_strategy_v2 import SignalGridStrategy


class BidirectionalGridRotationTask(StrategyV2):
    def __init__(self, long_strategy: SignalGridStrategy, short_strategy: SignalGridStrategy):
        super().__init__()
        self.config_path = "data/bidirectional_grid_rotation_task.json"
        self.short_strategy = short_strategy
        self.long_strategy = long_strategy
        self.rotation_increment = 10
        self.current_strategy = self.long_strategy
        self._init_current_strategy()
    
    def _init_current_strategy(self):
        # 判断config是否存在
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    config = json.load(f)
                    if config.get("current_strategy"):
                        self.rotation()
                        if config["current_strategy"] == "long" and self.current_strategy != self.long_strategy:
                            self.rotation()
            except Exception:
                pass

        if len(self.long_strategy.orders) > len(self.short_strategy.orders):
            self.rotation()
        else:
            self.current_strategy = self.short_strategy
            self.rotation()
    
    def is_order_full(self, strategy: SignalGridStrategy):
        return len(strategy.orders) >= strategy.config.max_order
    
    def reset_max_order(self, strategy: SignalGridStrategy):
        strategy.config.max_order = len(strategy.orders) + self.rotation_increment

    def rotation(self):
        negation_strategy = self.short_strategy if self.current_strategy == self.long_strategy else self.long_strategy
        self.reset_max_order(negation_strategy)
        self.current_strategy.config.max_order = 0
        self.current_strategy = negation_strategy

    def balance_max_order(self):
        max_order_diff = self.current_strategy.config.max_order - len(self.current_strategy.orders) - self.rotation_increment
        if max_order_diff > 0:
            self.current_strategy.config.max_order += max_order_diff

    def run(self, kline: Kline):
        if self.is_order_full(self.current_strategy):
            self.rotation()
            # 保存当前策略
            with open(self.config_path, "w") as f:
                json.dump({"current_strategy": self.current_strategy.config.position_side}, f)
        
        self.long_strategy.run(kline)
        self.short_strategy.run(kline)

        self.balance_max_order()
