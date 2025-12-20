import json
from client.ex_client import ExSwapClient
from model import Kline
from strategy import SingleTimeframeStrategy
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
import log
from pydantic import BaseModel
from typing import Literal

logger = log.getLogger(__name__)

class BidirectionalGridRotationStrategyConfig(BaseModel):
    long_strategy_config: SignalGridStrategyConfig
    short_strategy_config: SignalGridStrategyConfig
    default_strategy: Literal['long', 'short'] = 'long'
    rotation_increment: int = 10
    config_backup_path: str = 'bidirectional_grid_rotation_config.json'


class BidirectionalGridRotationStrategy(SingleTimeframeStrategy):
    def __init__(self, exchange_client: ExSwapClient, config: BidirectionalGridRotationStrategyConfig, timeframe: str):
        super().__init__(timeframe)
        self.config = config
        self.long_strategy = SignalGridStrategy(config.long_strategy_config, exchange_client, timeframe)
        self.short_strategy = SignalGridStrategy(config.short_strategy_config, exchange_client, timeframe)

        self.running_strategy = self.long_strategy if config.default_strategy == 'long' else self.short_strategy
        if self.is_order_full(self.running_strategy):
            self.reset_running_strategy()

        logger.info(f"BidirectionalGridRotation Start with {config.default_strategy}")
    
    def is_order_full(self, strategy: SignalGridStrategy):
        return len(strategy.order_manager.orders) >= strategy.config.max_order

    def reset_running_strategy(self):
        self.long_strategy.config.max_order = 0
        self.short_strategy.config.max_order = 0
        self.running_strategy.config.max_order = len(self.running_strategy.order_manager.orders) + self.config.rotation_increment

    def rotation(self):
        self.running_strategy = self.short_strategy if self.running_strategy == self.long_strategy else self.long_strategy
        self.reset_running_strategy()

    def balance_max_order(self):
        max_order_diff: int = self.running_strategy.config.max_order - len(self.running_strategy.order_manager.orders) - self.config.rotation_increment
        if max_order_diff > 0:
            self.running_strategy.config.max_order -= max_order_diff

    def run_strategy(self, kline: Kline):
        self.long_strategy.run(kline)
        self.short_strategy.run(kline)

    def run(self, kline: Kline):
        if self.is_order_full(self.running_strategy):
            self.rotation()
            logger.info(f"Rotation to {self.running_strategy.config.position_side}-{self.running_strategy.config.master_side.value}")
            with open(self.config.config_backup_path, "w") as f:
                json.dump({"current_strategy": self.running_strategy.config.position_side}, f)
        
        self.run_strategy(kline)

        self.balance_max_order()
