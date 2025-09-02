from model import Kline
from strategy import StrategyV2
from strategy.grids_strategy_v2 import SignalGridStrategy


class BidirectionalGridRotationTask(StrategyV2):
    def __init__(self, long_strategy: SignalGridStrategy, short_strategy: SignalGridStrategy):
        super().__init__()
        self.short_strategy = short_strategy
        self.long_strategy = long_strategy
        self.rotation_increment = 10
        self.current_strategy = self.long_strategy
        self.reset_current_strategy()
    
    def reset_current_strategy(self):
        if self.is_order_full(self.long_strategy) and self.is_order_full(self.short_strategy):
            if len(self.long_strategy.orders) > len(self.short_strategy.orders):
                self.current_strategy = self.short_strategy
            else:
                self.current_strategy = self.long_strategy
            self.increase_max_order(self.current_strategy)
    
    # 判断订单是否已满
    def is_order_full(self, strategy: SignalGridStrategy):
        return len(strategy.orders) >= strategy.config.max_order
    
    # 增加最大订单量
    def increase_max_order(self, strategy: SignalGridStrategy):
        strategy.config.max_order = len(strategy.orders) + self.rotation_increment

    def run(self, kline: Kline):
        negation_strategy = self.short_strategy if self.current_strategy == self.long_strategy else self.long_strategy
        if self.is_order_full(self.current_strategy):
            if self.is_order_full(negation_strategy):
                self.reset_current_strategy
            else:
                self.current_strategy = negation_strategy
        else:
            # 反向策略设置最大订单为0, 只会运行平仓逻辑, 用于关闭残留订单
            negation_strategy.config.max_order = 0
            negation_strategy.run(kline)
        
        self.current_strategy.run(kline)
