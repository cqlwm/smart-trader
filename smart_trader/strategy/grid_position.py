"""逻辑仓位领域对象: 只持策略参数与运行时状态, 价格 / 数量 / 相位由 orders 派生"""

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.model import OrderSide, PositionSide
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord

logger = log.getLogger(__name__)


class GridPosition(BaseModel):
    """逻辑仓位: 只持策略参数与运行时状态, 价格 / 数量 / 相位由 orders 派生"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    strategy_key: str
    symbol: str
    position_side: PositionSide
    main_order_side: OrderSide
    opening_quantity: float = 0.0
    opening_price: float = 0.0
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    enable_stop_loss: bool = False
    stop_loss_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.0
    trailing_stop_activation_rate: float = 0.0
    trailing_stop_price: float | None = None
    active: bool = True
    created_at: str = ""
    updated_at: str = ""
    orders: list[OrderRecord] = []

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other: object):
        if isinstance(other, GridPosition):
            return self.id == other.id
        return False

    # ---- 入/出场归类（按 position_side） ----

    @property
    def entry_side(self) -> OrderSide:
        return OrderSide.BUY if self.position_side == PositionSide.LONG else OrderSide.SELL

    @property
    def exit_side(self) -> OrderSide:
        return self.entry_side.reversal()

    @property
    def entry_orders(self) -> list[OrderRecord]:
        return [order for order in self.orders if order.side == self.entry_side]

    @property
    def exit_orders(self) -> list[OrderRecord]:
        return [order for order in self.orders if order.side == self.exit_side]

    # ---- 派生量（不落库） ----

    @property
    def entry_filled(self) -> float:
        return sum(order.filled_qty for order in self.entry_orders)

    @property
    def exit_filled(self) -> float:
        return sum(order.filled_qty for order in self.exit_orders)

    @property
    def quantity(self) -> float:
        return self.opening_quantity + self.entry_filled - self.exit_filled

    @property
    def price(self) -> float:
        """
        加权均价: (opening_price×opening_quantity, entry 行成交价×成交量)

        尚无成交（basis 为 0）时退回最近一条入场单的委托价 —— 未成交的入场单
        以委托价作为网格参考价, 否则空仓位的网格间隔 / 止盈判定会失去基准。
        """
        basis_qty = self.opening_quantity
        notional = self.opening_price * self.opening_quantity
        for order in self.entry_orders:
            if order.filled_qty <= 0:
                continue
            unit = order.avg_price if order.avg_price is not None else order.price
            if unit is None:
                continue
            notional += unit * order.filled_qty
            basis_qty += order.filled_qty
        if basis_qty <= 0:
            for order in reversed(self.entry_orders):
                if order.price is not None:
                    return order.price
            return self.opening_price
        return notional / basis_qty

    @property
    def pending_entry_orders(self) -> list[OrderRecord]:
        """仍未了结的入场单（status 为空或 OPEN）"""
        return [
            order
            for order in self.orders
            if order.side == self.entry_side
            and (order.status is None or order.status.is_open())
        ]

    @property
    def pending_exit_order(self) -> OrderRecord | None:
        """未成交的出场单（status 为空或 OPEN）; 每仓位至多一张"""
        for order in self.orders:
            if order.side == self.exit_side and (
                order.status is None or order.status.is_open()
            ):
                return order
        return None

    @property
    def has_pending_exit(self) -> bool:
        return self.pending_exit_order is not None

    @property
    def has_live_orders(self) -> bool:
        """是否存在任何存活订单（status 为空或 OPEN）"""
        return any(
            order.status is None or order.status.is_open() for order in self.orders
        )

    @property
    def phase(self) -> str:
        if not self.active:
            return "closed"
        if self.quantity <= 0:
            return "opening"
        if self.has_pending_exit:
            return "closing"
        return "holding"

    def profit_level(self, current_price: float) -> int:
        """
        计算逻辑仓位的盈利级别
        @param current_price 当前价格
        @return 表示盈利级别
            -1亏损中（或均价不可用）
            0盈利无法覆盖手续费
            1盈利中
            2达到止盈标准
        """
        if self.price <= 0:
            return -1
        compare_fun = self.entry_side.compare_fun()

        if compare_fun(current_price, self._profit(self.fixed_take_profit_rate)):
            return 2
        elif compare_fun(current_price, self._profit(self.signal_min_take_profit_rate)):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    def profit_and_loss_ratio(self, current_price: float) -> float:
        if self.price <= 0:
            return 0.0
        loss_rate = float(f"{abs(current_price - self.price) / self.price:.6f}")
        if self.profit_level(current_price) < 0:
            return -loss_rate
        return loss_rate

    def _profit(self, rate: float) -> float:
        rate_base = -1 if self.entry_side == OrderSide.SELL else 1
        return self.price * (1 + rate * rate_base)

    def to_row(self) -> GridPositionRow:
        """转换为账本行（不含 orders）"""
        return GridPositionRow(
            id=self.id,
            strategy_key=self.strategy_key,
            symbol=self.symbol,
            position_side=self.position_side,
            main_order_side=self.main_order_side,
            opening_quantity=self.opening_quantity,
            opening_price=self.opening_price,
            fixed_take_profit_rate=self.fixed_take_profit_rate,
            signal_min_take_profit_rate=self.signal_min_take_profit_rate,
            enable_stop_loss=self.enable_stop_loss,
            stop_loss_rate=self.stop_loss_rate,
            enable_trailing_stop=self.enable_trailing_stop,
            trailing_stop_rate=self.trailing_stop_rate,
            trailing_stop_activation_rate=self.trailing_stop_activation_rate,
            trailing_stop_price=self.trailing_stop_price,
            active=self.active,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_row(cls, row: GridPositionRow, orders: list[OrderRecord]) -> "GridPosition":
        """从账本行 + 关联订单还原"""
        return cls(
            id=row.id,
            strategy_key=row.strategy_key,
            symbol=row.symbol,
            position_side=row.position_side,
            main_order_side=row.main_order_side,
            opening_quantity=row.opening_quantity,
            opening_price=row.opening_price,
            fixed_take_profit_rate=row.fixed_take_profit_rate,
            signal_min_take_profit_rate=row.signal_min_take_profit_rate,
            enable_stop_loss=row.enable_stop_loss,
            stop_loss_rate=row.stop_loss_rate,
            enable_trailing_stop=row.enable_trailing_stop,
            trailing_stop_rate=row.trailing_stop_rate,
            trailing_stop_activation_rate=row.trailing_stop_activation_rate,
            trailing_stop_price=row.trailing_stop_price,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            orders=list(orders),
        )


def load_positions(journal: OrderJournal, strategy_key: str) -> list[GridPosition]:
    """从账本还原某策略的全部活跃逻辑仓位（含关联订单）"""
    rows = journal.load_active_positions(strategy_key)
    orders = journal.load_orders_for_positions([row.id for row in rows])
    by_position: dict[str, list[OrderRecord]] = {}
    for order in orders:
        by_position.setdefault(order.grid_position_id, []).append(order)
    logger.info("从账本加载逻辑仓位: %s 笔", len(rows))
    return [GridPosition.from_row(row, by_position.get(row.id, [])) for row in rows]
