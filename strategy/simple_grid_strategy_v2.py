import secrets
import threading
import numpy as np
from typing import List
from datetime import datetime

from pydantic import BaseModel
from strategy import StrategyV2
from client.ex_client import ExSwapClient, ExClient
from model import PositionSide, Symbol, OrderSide, OrderStatus
import log

logger = log.getLogger(__name__)


class OrderPair(BaseModel):
    position_side: PositionSide
    side: OrderSide
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
            if self.position_side == PositionSide.LONG:
                # 做多: 低买高卖
                return (self.exit_price - self.entry_price) * self.quantity
            else:
                # 做空: 高卖低买
                return (self.entry_price - self.exit_price) * self.quantity
        return 0.0

    def is_complete(self) -> bool:
        """检查订单对是否完成"""
        return self.entry_filled and self.exit_filled

    def update_order_status(self, client: ExClient):
        """更新订单状态"""
        if self.entry_order_id and not self.entry_filled:
            try:
                entry_order = client.query_order(self.entry_order_id, self.symbol)
                if entry_order.get('status') == OrderStatus.CLOSED.value:
                    self.entry_filled = True
                    logger.info(f"开仓单完成: {self.entry_order_id} @ {self.entry_price}")
            except Exception as e:
                logger.error(f"查询开仓单状态失败: {e}")

        if self.exit_order_id and not self.exit_filled:
            try:
                exit_order = client.query_order(self.exit_order_id, self.symbol)
                if exit_order.get('status') == OrderStatus.CLOSED.value:
                    self.exit_filled = True
                    logger.info(f"平仓单完成: {self.exit_order_id} @ {self.exit_price}")
            except Exception as e:
                logger.error(f"查询平仓单状态失败: {e}")

        if self.is_complete():
            self.total_profit += self.calculate_profit()

    def run(self, client: ExClient):
        """执行订单对套利"""
        if self.position_side == PositionSide.LONG:
            entry_side = OrderSide.BUY
            exit_side = OrderSide.SELL
        else:
            entry_side = OrderSide.SELL
            exit_side = OrderSide.BUY

        # 检查是否需要下开仓单
        if not self.entry_order_id:
            self._place_order(client, "entry", entry_side, self.entry_price)
        # 检查是否需要下平仓单
        elif self.entry_filled and not self.exit_order_id:
            self._place_order(client, "exit", exit_side, self.exit_price)

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
                position_side=self.position_side,
            )
            if order:
                order_id = order.get('clientOrderId', '')
                if order_type == "entry":
                    self.entry_order_id = order_id
                else:
                    self.exit_order_id = order_id
                logger.info(f"{order_type}: {order_id} @ {price}")
        except Exception as _:
            logger.error(f"{order_type}失败", exc_info=True)

    def cancel_orders(self, client: ExClient) -> bool:
        """取消未成交的订单"""
        entry_cancelled = False
        exit_cancelled = False

        if self.entry_order_id and not self.entry_filled:
            try:
                client.cancel(self.entry_order_id, self.symbol)
                logger.info(f"取消开仓单: {self.entry_order_id}")
                entry_cancelled = True
            except Exception as e:
                logger.error(f"取消开仓单失败: {e}")

        if self.exit_order_id and not self.exit_filled:
            try:
                client.cancel(self.exit_order_id, self.symbol)
                logger.info(f"取消平仓单: {self.exit_order_id}")
                exit_cancelled = True
            except Exception as e:
                logger.error(f"取消平仓单失败: {e}")

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
        logger.info(f"重置订单对: {self.position_side.value} 开仓价 {self.entry_price}, 平仓价 {self.exit_price}, 累积盈利 {self.total_profit}")

    def can_run(self) -> bool:
        """检查订单对是否可以运行（未完成状态）"""
        return not self.is_complete()

class SimpleGridStrategy(StrategyV2):
    def __init__(self, ex_client: ExSwapClient, symbol: Symbol,
                 upper_price: float, lower_price: float, grid_num: int,
                 quantity_per_grid: float, position_side: PositionSide,
                 active_grid_count: int = 5):
        super().__init__()
        self.ex_client = ex_client
        self.symbol = symbol
        self.upper_price = upper_price
        self.lower_price = lower_price
        self.grid_num = grid_num
        self.quantity = quantity_per_grid
        self.position_side = position_side
        self.active_grid_count = active_grid_count  # 激活的网格数量
        self.grids: List[OrderPair] = []
        self.lock = threading.Lock()

    def _calculate_grid_prices(self) -> List[float]:
        """计算网格价格"""
        return list(np.linspace(self.lower_price, self.upper_price, self.grid_num))

    def get_active_grid_indices(self, current_price: float) -> List[int]:
        """根据当前价格获取应该激活的网格索引"""
        if not self.grids:
            return []

        # 找到当前价格所在的网格区间
        current_grid_index = self._find_current_grid_index(current_price)

        # 计算激活范围: 当前网格上下各激活 active_grid_count//2 个网格
        half_count = self.active_grid_count // 2
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
        for index, grid in enumerate(self.grids):
            if self.position_side == PositionSide.LONG:
                # 做多: entry_price < exit_price
                if grid.entry_price <= current_price <= grid.exit_price:
                    return index
            else:
                # 做空: entry_price > exit_price
                if grid.exit_price <= current_price <= grid.entry_price:
                    return index

        # 如果当前价格不在任何网格内，返回最接近的网格
        if current_price < self.lower_price:
            return 0
        elif current_price > self.upper_price:
            return len(self.grids) - 1
        else:
            # 找到最接近的网格
            min_distance = float('inf')
            closest_index = 0
            for index, grid in enumerate(self.grids):
                mid_price = (grid.entry_price + grid.exit_price) / 2
                distance = abs(current_price - mid_price)
                if distance < min_distance:
                    min_distance = distance
                    closest_index = index
            return closest_index

    def cancel_inactive_grids(self, current_price: float, active_indices: List[int]):
        """取消远离当前价格的网格订单"""
        # active_indices = set(self.get_active_grid_indices(current_price))

        for index, grid in enumerate(self.grids):
            if index not in active_indices and not grid.is_complete():
                # 取消不在激活范围内的订单
                if grid.cancel_orders(self.ex_client):
                    logger.info(f"取消远离价格的网格 {index}: 当前价格 {current_price}, 网格范围 [{grid.entry_price}, {grid.exit_price}]")

    def get_current_price(self) -> float:
        """获取当前市场价格"""
        return self.last_kline.close

    def initialize_grids(self):
        """初始化网格订单对"""
        if self.grids:
            return

        grid_prices = self._calculate_grid_prices()

        for index in range(len(grid_prices) - 1):
            if self.position_side == PositionSide.LONG:
                # 做多: 低价开仓买入，高价平仓卖出
                entry_price = grid_prices[index]
                exit_price = grid_prices[index + 1]
                side = OrderSide.BUY
            else:
                # 做空: 高价开仓卖出，低价平仓买入
                entry_price = grid_prices[index + 1]
                exit_price = grid_prices[index]
                side = OrderSide.SELL

            order_pair = OrderPair(
                position_side=self.position_side,
                side=side,
                symbol=self.symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=self.quantity
            )
            self.grids.append(order_pair)
        

    def update_grid_orders(self):
        """更新网格订单状态"""
        current_price = self.get_current_price()

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
            self.cancel_inactive_grids(current_price, active_indices)

    def get_total_profit(self) -> float:
        """获取总盈利"""
        return sum(grid.total_profit for grid in self.grids)

    def run_strategy(self):
        """运行策略"""
        self.initialize_grids()
        self.update_grid_orders()

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
