import secrets
import threading
import numpy as np
from typing import List
from datetime import datetime, timezone

from pydantic import BaseModel
from strategy import SimpleStrategy
from client.ex_client import ExSwapClient, ExClient
from model import PlaceOrderBehavior, PositionSide, Symbol, OrderSide, OrderStatus
import log
from config import DATA_PATH
import builtins
from persistence.repository import StrategyRepository
from persistence.sqlite_repo import SQLiteStrategyRepository
from strategy.registry import register_strategy

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
    strategy_id: str = ""

    def to_db_dict(self) -> dict:
        order_id = self.entry_order_id if self.entry_order_id else f"temp_{secrets.token_hex(8)}"

        status = "pending"
        if self.is_complete():
            status = "completed"
        elif self.entry_filled:
            status = "entry_filled"

        return {
            'id': order_id,
            'symbol': self.symbol.simple(),
            'position_side': self.position_side.value,
            'order_side': self.entry_side.value,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'quantity': self.quantity,
            'entry_order_id': self.entry_order_id,
            'exit_order_id': self.exit_order_id,
            'status': status,
            'extra_data': {
                'total_profit': self.total_profit,
                'entry_filled': self.entry_filled,
                'exit_filled': self.exit_filled
            }
        }

    @classmethod
    def from_db_dict(cls, data: dict, symbol_obj: Symbol) -> 'OrderPair':
        extra = data.get('extra_data', {})

        return cls(
            position_side=PositionSide(data['position_side']),
            entry_side=OrderSide(data['order_side']),
            symbol=symbol_obj,
            entry_price=data['entry_price'],
            exit_price=data['exit_price'],
            quantity=data['quantity'],
            entry_order_id=data.get('entry_order_id', ""),
            exit_order_id=data.get('exit_order_id', ""),
            entry_filled=extra.get('entry_filled', False),
            exit_filled=extra.get('exit_filled', False),
            total_profit=extra.get('total_profit', 0.0)
        )

    def calculate_profit(self) -> float:
        if self.entry_filled and self.exit_filled:
            return abs(self.exit_price - self.entry_price) * self.quantity
        return 0.0

    def is_complete(self) -> bool:
        return self.entry_filled and self.exit_filled

    def update_order_status(self, client: ExClient):
        status_snapshot = self.is_complete()
        if status_snapshot:
            return

        if self.entry_order_id and not self.entry_filled:
            try:
                entry_order = client.query_order(self.entry_order_id, self.symbol)
                if entry_order and entry_order.status == OrderStatus.CLOSED:
                    self.entry_filled = True
                    logger.info(f"开仓单完成:{self.symbol.binance()} {self.entry_order_id} @ {self.entry_price}")
                elif entry_order and entry_order.status == OrderStatus.CANCELED:
                    self.entry_order_id = ""
                    self.entry_filled = False
            except Exception as e:
                logger.error(f"查询开仓单状态失败: {self.symbol.binance()} {self.entry_order_id} {e}")

        if self.exit_order_id and not self.exit_filled:
            try:
                exit_order = client.query_order(self.exit_order_id, self.symbol)
                if exit_order and exit_order.status == OrderStatus.CLOSED:
                    self.exit_filled = True
                    logger.info(f"平仓单完成:{self.symbol.binance()} {self.exit_order_id} @ {self.exit_price}")
                elif exit_order and exit_order.status == OrderStatus.CANCELED:
                    self.exit_order_id = ""
                    self.exit_filled = False
            except Exception as e:
                logger.error(f"查询平仓单状态失败: {self.symbol.binance()} {self.exit_order_id} {e}")

        if not status_snapshot and self.is_complete():
            self.total_profit += self.calculate_profit()

    def run(self, client: ExClient):
        if not self.entry_order_id:
            self._place_order(client, "entry", self.entry_side, self.entry_price)
        elif self.entry_filled and not self.exit_order_id:
            self._place_order(client, "exit", self.entry_side.reversal(), self.exit_price)

        self.update_order_status(client)

    def _place_order(self, client: ExClient, order_type: str, side: OrderSide, price: float):
        try:
            custom_id = f"{order_type}_{int(datetime.now(timezone.utc).timestamp())}_{secrets.token_hex(nbytes=1)}"
            order = client.place_order_v2(
                strategy_id=self.strategy_id,
                custom_id=custom_id,
                symbol=self.symbol,
                order_side=side,
                quantity=self.quantity,
                price=price,
                position_side=self.position_side
            )
            if order:
                if order_type == "entry":
                    self.entry_order_id = order.order_id
                else:
                    self.exit_order_id = order.order_id
                logger.info(f"{self.symbol.binance()} {order_type} {order.order_id} @ {price}")
        except Exception as e:
            logger.error(f"订单失败: {self.symbol.binance()} {order_type} {e}", exc_info=True)

    @staticmethod
    def place_order(client: ExClient, symbol: Symbol, position_side: PositionSide, order_side: OrderSide, quantity: float, strategy_id: str = "") -> str:
        try:
            order = client.place_order_v2(
                strategy_id=strategy_id,
                custom_id=f"{int(datetime.now(timezone.utc).timestamp())}_{secrets.token_hex(nbytes=1)}",
                symbol=symbol,
                order_side=order_side,
                quantity=quantity,
                position_side=position_side,
                place_order_behavior=PlaceOrderBehavior.CHASER
            )
            if order:
                return order.order_id
            else:
                raise Exception("下单失败 order is None")
        except Exception as e:
            logger.error("下单失败", exc_info=True)
            raise e


    def cancel_orders(self, client: ExClient) -> bool:
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

        if entry_cancelled:
            self.entry_order_id = ""
            self.entry_filled = False

        if exit_cancelled:
            self.exit_order_id = ""
            self.exit_filled = False

        return entry_cancelled or exit_cancelled

    def reset(self):
        self.entry_order_id = ""
        self.exit_order_id = ""
        self.entry_filled = False
        self.exit_filled = False
        logger.info(f"重置订单对:{self.symbol.binance()} {self.position_side.name}, 入场 {self.entry_side.name}_{self.entry_price}, 退出 {self.entry_side.reversal().name}_{self.exit_price}, 累积盈利 {self.total_profit}")

    def can_run(self) -> bool:
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


@register_strategy("simple_grid", SimpleGridStrategyConfig)
class SimpleGridStrategy(SimpleStrategy):
    def __init__(self, ex_client: ExSwapClient, config: SimpleGridStrategyConfig, timeframe: str, repository: StrategyRepository | None = None):
        super().__init__(config.symbol, timeframe)
        self.config = config
        self.ex_client = ex_client
        self.grids: List[OrderPair] = []
        self.lock = threading.Lock()
        self.strategy_id = f"simple_grid_{self.config.symbol.simple()}_{self.config.position_side.value}_{self.config.master_order_side.value}"
        self.order_repo = ex_client.order_repo
        self._repository = repository or SQLiteStrategyRepository()

        self._repository.save_strategy_instance(
            strategy_id=self.strategy_id,
            strategy_type="simple_grid",
            symbol=self.config.symbol.simple(),
            config_data=self.config.model_dump_json()
        )

        if self.config.backup_file:
            self.backup_file = self.config.backup_file
        else:
            self.backup_file = f"{DATA_PATH}/backup_{self.config.symbol.simple()}_{self.config.position_side.value}_{self.config.master_order_side.value}.json"
        self.load_state()

    def load_state(self):
        try:
            db_orders = self._repository.load_active_orders(self.strategy_id)
            if db_orders:
                self.grids = [OrderPair.from_db_dict(d, self.config.symbol) for d in db_orders]
                for grid in self.grids:
                    grid.strategy_id = self.strategy_id
                logger.info(f"从数据库加载 {len(self.grids)} 个{self.config.symbol.binance()}网格")
            else:
                logger.info(f"数据库中没有 {self.strategy_id} 的状态，初始化空状态")
        except Exception as e:
            logger.error(f"从数据库加载状态失败 {self.strategy_id}: {e}")

    def save_state(self):
        try:
            db_orders = [grid.to_db_dict() for grid in self.grids]
            self._repository.save_active_orders(self.strategy_id, db_orders)
        except Exception as e:
            logger.error(f"保存状态到数据库失败 {self.strategy_id}: {e}")

    def _calculate_grid_prices(self) -> List[float]:
        return list(np.linspace(self.config.lower_price, self.config.upper_price, self.config.grid_num))

    def get_active_grid_indices(self, current_price: float) -> List[int]:
        if not self.grids:
            return []

        current_grid_index = self._find_current_grid_index(current_price)

        half_count = self.config.active_grid_count // 2

        indices = [current_grid_index]
        for i in range(1, half_count + 1):
            if current_grid_index - i >= 0:
                indices.append(current_grid_index - i)
            if current_grid_index + i < len(self.grids):
                indices.append(current_grid_index + i)

        return indices

    def _find_current_grid_index(self, current_price: float) -> int:
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
        for index, grid in enumerate(self.grids):
            if index not in active_indices and not grid.is_complete():
                grid.cancel_orders(self.ex_client)

    def get_current_price(self) -> float:
        return self.latest_kline_obj.close

    def initialize_grids(self):
        if self.grids:
            return

        grid_prices = self._calculate_grid_prices()

        for index in range(len(grid_prices) - 1):
            if self.config.master_order_side == OrderSide.BUY:
                entry_price = grid_prices[index]
                exit_price = grid_prices[index + 1]
            else:
                entry_price = grid_prices[index + 1]
                exit_price = grid_prices[index]

            order_pair = OrderPair(
                position_side=self.config.position_side,
                entry_side=self.config.master_order_side,
                symbol=self.config.symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=self.config.quantity_per_grid,
                strategy_id=self.strategy_id
            )
            self.grids.append(order_pair)

        if not self.config.delay_pending_order:
            current_price = self.latest_kline_obj.close
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
                    quantity=order_quantity,
                    strategy_id=self.strategy_id
                )
                for grid in run_grids:
                    if not grid.entry_filled:
                        grid.entry_order_id = order_id

    def update_grid_orders(self):
        current_price = self.get_current_price()

        active_indices = self.get_active_grid_indices(current_price)

        has_complete_grid = False
        for index in active_indices:
            grid = self.grids[index]
            if grid.is_complete():
                try:
                    self._repository.append_trade_history(
                        strategy_id=self.strategy_id,
                        trade_record={
                            'symbol': grid.symbol.ccxt(),
                            'entry_order_id': grid.entry_order_id,
                            'exit_order_id': grid.exit_order_id,
                            'entry_price': grid.entry_price,
                            'exit_price': grid.exit_price,
                            'quantity': grid.quantity,
                            'profit': grid.calculate_profit()
                        }
                    )
                except Exception as e:
                    logger.error(f"记录交易历史失败: {e}")

                grid.reset()
                has_complete_grid = True
            grid.run(self.ex_client)

        if has_complete_grid:
            self.cancel_inactive_grids(active_indices)

    def get_total_profit(self) -> float:
        return sum(grid.total_profit for grid in self.grids)

    def run_strategy(self):
        self.initialize_grids()

        current_price = self.get_current_price()
        if current_price < self.config.lower_price * 0.99 or current_price > self.config.upper_price * 1.01:
            return

        self.update_grid_orders()
        self.save_state()

    def _on_kline(self):
        if self.lock.acquire(blocking=False):
            try:
                self.run_strategy()
            finally:
                self.lock.release()

    def _on_kline_finished(self):
        pass
