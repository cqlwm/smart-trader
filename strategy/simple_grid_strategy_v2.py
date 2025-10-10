import secrets
import threading
import numpy as np
import os
from typing import List
from datetime import datetime

from pydantic import BaseModel
from strategy import StrategyV2
from client.ex_client import ExSwapClient, ExClient
from model import PlaceOrderBehavior, PositionSide, Symbol, OrderSide, OrderStatus
import log
from config import DATA_PATH
import builtins

logger = log.getLogger(__name__)

class OrderPair(BaseModel):
    position_side: PositionSide
    entry_side: OrderSide
    symbol: Symbol
    entry_price: float
    exit_price: float
    quantity: float
    total_profit: float = 0.0
    entry_order_id: str = ""
    exit_order_id: str = ""
    entry_filled: bool = False
    exit_filled: bool = False

    def calculate_profit(self) -> float:
        """计算套利盈利"""
        if self.entry_filled and self.exit_filled:
            return abs(self.exit_price - self.entry_price) * self.quantity
        return 0.0

    def is_complete(self) -> bool:
        """检查订单对是否完成"""
        return self.entry_filled and self.exit_filled

    def update_order_status(self, client: ExClient):
        """更新订单状态"""
        # 状态快照
        status_snapshot = self.is_complete()
        if status_snapshot:
            return

        if self.entry_order_id and not self.entry_filled:
            try:
                entry_order = client.query_order(self.entry_order_id, self.symbol)
                if entry_order.get('status') == OrderStatus.CLOSED.value:
                    self.entry_filled = True
                    logger.info(f"开仓单完成:{self.symbol.binance()} {self.entry_order_id} @ {self.entry_price}")
                elif entry_order.get('status') == OrderStatus.CANCELED.value:
                    self.entry_order_id = ""
                    self.entry_filled = False
            except Exception as e:
                logger.error(f"查询开仓单状态失败: {self.symbol.binance()} {self.entry_order_id} {e}")

        if self.exit_order_id and not self.exit_filled:
            try:
                exit_order = client.query_order(self.exit_order_id, self.symbol)
                if exit_order.get('status') == OrderStatus.CLOSED.value:
                    self.exit_filled = True
                    logger.info(f"平仓单完成:{self.symbol.binance()} {self.exit_order_id} @ {self.exit_price}")
                elif exit_order.get('status') == OrderStatus.CANCELED.value:
                    self.exit_order_id = ""
                    self.exit_filled = False
            except Exception as e:
                logger.error(f"查询平仓单状态失败: {self.symbol.binance()} {self.exit_order_id} {e}")

        if not status_snapshot and self.is_complete():
            self.total_profit += self.calculate_profit()

    def run(self, client: ExClient):
        """执行订单对套利"""
        # 检查是否需要下开仓单
        if not self.entry_order_id:
            self._place_order(client, "entry", self.entry_side, self.entry_price)
        # 检查是否需要下平仓单
        elif self.entry_filled and not self.exit_order_id:
            self._place_order(client, "exit", self.entry_side.reversal(), self.exit_price)

        # 更新订单状态
        self.update_order_status(client)

    def _place_order(self, client: ExClient, order_type: str, side: OrderSide, price: float):
        """通用下单方法"""
        try:
            custom_id = f"{order_type}_{int(datetime.now().timestamp())}_{secrets.token_hex(nbytes=1)}"
            order = client.place_order_v2(
                custom_id=custom_id,
                symbol=self.symbol,
                order_side=side,
                quantity=self.quantity,
                price=price,
                position_side=self.position_side
            )
            if order:
                order_id = order.get('clientOrderId', '')
                if order_type == "entry":
                    self.entry_order_id = order_id
                else:
                    self.exit_order_id = order_id
                logger.info(f"{self.symbol.binance()} {order_type} {order_id} @ {price}")
        except Exception as e:
            logger.error(f"订单失败: {self.symbol.binance()} {order_type} {e}", exc_info=True)

    @staticmethod
    def place_order(client: ExClient, symbol: Symbol, position_side: PositionSide, order_side: OrderSide, quantity: float) -> str:
        """通用下单方法"""
        try:
            order = client.place_order_v2(
                custom_id=f"{int(datetime.now().timestamp())}_{secrets.token_hex(nbytes=1)}",
                symbol=symbol,
                order_side=order_side,
                quantity=quantity,
                position_side=position_side,
                place_order_behavior=PlaceOrderBehavior.CHASER
            )
            if order:
                order_id: str = order.get('clientOrderId', '')
                return order_id
            else:
                raise Exception("下单失败 order is None")
        except Exception as e:
            logger.error("下单失败", exc_info=True)
            raise e


    def cancel_orders(self, client: ExClient) -> bool:
        """取消未成交的订单"""
        entry_cancelled = False
        exit_cancelled = False

        self.update_order_status(client)

        if self.entry_order_id and not self.entry_filled:
            try:
                client.cancel(self.entry_order_id, self.symbol)
                logger.info(f"取消开仓单:{self.symbol.binance()} {self.entry_order_id}")
                entry_cancelled = True
            except Exception as e:
                logger.error(f"取消开仓单失败:{self.symbol.binance()} {e}")

        if self.exit_order_id and not self.exit_filled:
            try:
                client.cancel(self.exit_order_id, self.symbol)
                logger.info(f"取消平仓单:{self.symbol.binance()} {self.exit_order_id}")
                exit_cancelled = True
            except Exception as e:
                logger.error(f"取消平仓单失败:{self.symbol.binance()} {e}")

        # 重置被取消订单的状态
        if entry_cancelled:
            self.entry_order_id = ""
            self.entry_filled = False

        if exit_cancelled:
            self.exit_order_id = ""
            self.exit_filled = False
        
        return entry_cancelled or exit_cancelled

    def reset(self):
        """重置订单对状态，用于重新开始交易，保留累积盈利"""
        self.entry_order_id = ""
        self.exit_order_id = ""
        self.entry_filled = False
        self.exit_filled = False
        logger.info(f"重置订单对:{self.symbol.binance()} {self.position_side.name}, 入场 {self.entry_side.name}_{self.entry_price}, 退出 {self.entry_side.reversal().name}_{self.exit_price}, 累积盈利 {self.total_profit}")

    def can_run(self) -> bool:
        """检查订单对是否可以运行（未完成状态）"""
        return not self.is_complete()

class OrderPairListModel(BaseModel):
    items: List[OrderPair] = []

class SimpleGridStrategyConfig(BaseModel):
    symbol: Symbol
    upper_price: float
    lower_price: float
    grid_num: int
    quantity_per_grid: float
    active_grid_count: int = 5
    position_side: PositionSide = PositionSide.LONG
    master_order_side: OrderSide = OrderSide.BUY
    delay_pending_order: bool = False
    initial_quota: float = 0
    backup_file: str = ""


class SimpleGridStrategy(StrategyV2):
    def __init__(self, ex_client: ExSwapClient, config: SimpleGridStrategyConfig):
        super().__init__()
        self.config = config
        self.ex_client = ex_client
        self.grids: List[OrderPair] = []
        self.lock = threading.Lock()
        if self.config.backup_file:
            self.backup_file = self.config.backup_file
        else:
            self.backup_file = f"{DATA_PATH}/backup_{self.config.symbol.simple()}_{self.config.position_side.value}_{self.config.master_order_side.value}.json"
        self.load_state()

    def load_state(self):
        """从备份文件加载状态"""
        try:
            if not os.path.exists(self.backup_file):
                return
            with open(self.backup_file, 'r') as f:
                json_str = f.read()
                data = OrderPairListModel.model_validate_json(json_str)
                self.grids = data.items
                logger.info(f"从备份文件加载 {len(self.grids)} 个{self.config.symbol.binance()}网格")
        except FileNotFoundError:
            logger.info(f"备份文件 {self.backup_file} 不存在，初始化空状态")
        except Exception as e:
            logger.error(f"加载备份文件 {self.backup_file} 失败: {e}")

    def save_state(self):
        """将当前状态保存到备份文件"""
        try:
            with open(self.backup_file, 'w') as f:
                data = OrderPairListModel(items=self.grids)
                json_str = data.model_dump_json(indent=2)
                f.write(json_str)
                # logger.info(f"保存 {len(self.grids)} 个网格到备份文件 {self.backup_file}")
        except Exception as e:
            logger.error(f"保存备份文件 {self.backup_file} 失败: {e}")

    def _calculate_grid_prices(self) -> List[float]:
        """计算网格价格"""
        return list(np.linspace(self.config.lower_price, self.config.upper_price, self.config.grid_num))

    def get_active_grid_indices(self, current_price: float) -> List[int]:
        """根据当前价格获取应该激活的网格索引"""
        if not self.grids:
            return []

        # 找到当前价格所在的网格区间
        current_grid_index = self._find_current_grid_index(current_price)

        # 计算激活范围: 当前网格上下各激活 active_grid_count//2 个网格
        half_count = self.config.active_grid_count // 2
        # start_index = max(0, current_grid_index - half_count)
        # end_index = min(len(self.grids), current_grid_index + half_count + 1)

        indices = [current_grid_index]
        for i in range(1, half_count + 1):
            if current_grid_index - i >= 0:
                indices.append(current_grid_index - i)
            if current_grid_index + i < len(self.grids):
                indices.append(current_grid_index + i)
            
        return indices

    def _find_current_grid_index(self, current_price: float) -> int:
        """找到当前价格所在的网格区间索引"""
        # 如果当前价格不在任何网格内，返回最接近的网格
        if current_price <= self.config.lower_price:
            return 0
        elif current_price >= self.config.upper_price:
            return len(self.grids) - 1
        else:
            for index, grid in enumerate(self.grids):
                if (grid.entry_price <= current_price <= grid.exit_price) or (grid.exit_price <= current_price <= grid.entry_price):
                    return index
            raise ValueError(f"预料之外的错误, 当前价格 {current_price} 不在任何网格内")

    def cancel_inactive_grids(self, active_indices: List[int]):
        """取消远离当前价格的网格订单"""

        for index, grid in enumerate(self.grids):
            if index not in active_indices and not grid.is_complete():
                # 取消不在激活范围内的订单
                grid.cancel_orders(self.ex_client)
                # logger.info(f"取消远离价格的网格 {index}: 当前价格 {current_price}, 网格范围 [{grid.entry_price}, {grid.exit_price}]")

    def get_current_price(self) -> float:
        """获取当前市场价格"""
        return self.last_kline.close

    def initialize_grids(self):
        """初始化网格订单对"""
        if self.grids:
            return

        grid_prices = self._calculate_grid_prices()

        for index in range(len(grid_prices) - 1):
            if self.config.master_order_side == OrderSide.BUY:
                # 做多: 低价开仓买入，高价平仓卖出
                entry_price = grid_prices[index]
                exit_price = grid_prices[index + 1]
            else:
                # 做空: 高价开仓卖出，低价平仓买入
                entry_price = grid_prices[index + 1]
                exit_price = grid_prices[index]

            order_pair = OrderPair(
                position_side=self.config.position_side,
                entry_side=self.config.master_order_side,
                symbol=self.config.symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=self.config.quantity_per_grid
            )
            self.grids.append(order_pair)
        
        if not self.config.delay_pending_order:
            current_price = self.last_kline.close
            compare = builtins.float.__le__ if self.config.master_order_side == OrderSide.BUY else builtins.float.__ge__
            run_grids = list(filter(lambda grid: compare(current_price, grid.entry_price), self.grids))
            
            real_quota = 0
            for grid in run_grids:
                if self.config.initial_quota >= real_quota + grid.quantity:
                    real_quota += grid.quantity
                else:
                    break
                grid.entry_order_id = '-'
                grid.entry_filled = True

            order_quantity = len(run_grids) * self.config.quantity_per_grid - real_quota
            if order_quantity > 0:
                order_id = OrderPair.place_order(
                    client=self.ex_client,
                    symbol=self.config.symbol,
                    position_side=self.config.position_side,
                    order_side=self.config.master_order_side,
                    quantity=order_quantity
                )
                for grid in run_grids:
                    if not grid.entry_filled:
                        grid.entry_order_id = order_id

    def update_grid_orders(self):
        """更新网格订单状态"""
        current_price = self.get_current_price()

        if current_price < self.config.lower_price * 0.99 or current_price > self.config.upper_price * 1.01:
            return

        # 获取应该激活的网格索引
        active_indices = self.get_active_grid_indices(current_price)

        # 只更新激活范围内的网格
        has_complete_grid = False
        for index in active_indices:
            grid = self.grids[index]
            if grid.is_complete():
                grid.reset()
                has_complete_grid = True
            grid.run(self.ex_client)

        # 取消远离当前价格的订单
        if has_complete_grid:
            self.cancel_inactive_grids(active_indices)

    def get_total_profit(self) -> float:
        """获取总盈利"""
        return sum(grid.total_profit for grid in self.grids)

    def run_strategy(self):
        """运行策略"""
        self.initialize_grids()
        self.update_grid_orders()
        self.save_state()

    def on_kline(self):
        """每次K线更新时调用"""
        if self.lock.acquire(blocking=False):
            try:
                self.run_strategy()
            finally:
                self.lock.release()

    def on_kline_finished(self):
        """K线完成时调用"""
        pass
