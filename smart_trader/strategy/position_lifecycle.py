"""订单生命周期: 结算 / 丢弃 / 撤单复查 / 入场单了结 / 出场单跟踪

入场单的两条结算路径职责互斥（设计见 .memory/03-coding-design.md 三.12）:

- `settle_entry_fills`（盘中每 tick）: 只查询并回写成交事实, 不撤单 ——
  入场单由追单器刻意挂出, 盘中撤掉等于推翻追单成果;
- `settle_pending_entries`（出场流程）: 撤剩余入场单防继续建仓, 再按终态结算。
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from smart_trader import log
from smart_trader.exceptions import SmartTraderError
from smart_trader.exchange.ports import TradingPort
from smart_trader.model import OrderResult, OrderStatus
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import OrderRecord
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig

logger = log.getLogger(__name__)

# 查询不到的订单按 EXPIRED 结算前的最小年龄(防交易所 settle 延迟),
# 与 OrderReconciler.min_age_seconds 同口径
_MIN_QUERY_AGE_SECONDS = 5.0


class PositionLifecycle:
    """仓位订单的结算与出场单跟踪（账本 + 内存同步）"""

    def __init__(
        self,
        config: SignalGridStrategyConfig,
        trading: TradingPort,
        journal: OrderJournal,
        positions: Callable[[], list[GridPosition]],
    ):
        self._config = config
        self._trading = trading
        self._journal = journal
        self._positions = positions

    def settle_order(
        self, position: GridPosition, order: OrderRecord, result: OrderResult
    ) -> None:
        """用交易所事实回写一条订单行（账本 + 内存）"""
        updated = order.model_copy(
            update={
                "price": result.price if result.price is not None else order.price,
                "quantity": (
                    result.quantity if result.quantity > 0 else order.quantity
                ),
                "filled_qty": result.filled_qty,
                "avg_price": result.avg_price,
                "status": result.status,
            }
        )
        self._journal.settle_order(updated)
        position.orders = [
            updated if item.order_id == updated.order_id else item
            for item in position.orders
        ]

    def discard_order(self, position: GridPosition, order_id: str) -> None:
        """交易所从未存在该订单: 删行并从内存移除"""
        self._journal.discard_order(order_id)
        position.orders = [item for item in position.orders if item.order_id != order_id]

    def cancel_and_refresh(self, custom_id: str) -> OrderResult | None:
        """
        撤掉未成交完的挂单并复查终态

        撤单请求发出前可能又有成交, 因此复查一次拿最终的成交量与状态;
        复查失败(返回 None)时由调用方沿用已知状态。
        """
        self._trading.cancel(custom_id, self._config.symbol)
        return self._trading.query(custom_id, self._config.symbol)

    def settle_entry_fills(self, position: GridPosition) -> None:
        """查询并回写入场单的成交事实（盘中路径, 每 tick 无条件调用）

        只 settle 不撤单: 未成交入场单继续挂在交易所等成交;
        撤销未成交入场单（防继续建仓）属于出场流程（settle_pending_entries）。

        容错语义与出场单跟踪同源:
        - 查询异常 → 跳过, 下一根再试;
        - 查询不到 → 距创建过近先跳过（防交易所 settle 延迟）, 足够老才按
          EXPIRED 结算（交易所从未存在该单）; created_at 为空的内存行按过近处理。
        """
        for order in list(position.pending_entry_orders):
            try:
                result = self._trading.query(order.order_id, self._config.symbol)
            except SmartTraderError:
                logger.warning("查询入场单失败, 保留待结算: %s", order.order_id)
                continue
            if result is None:
                if self._too_fresh(order.created_at):
                    continue
                self.settle_order(
                    position,
                    order,
                    OrderResult(
                        custom_id=order.order_id,
                        symbol=self._config.symbol,
                        side=order.side,
                        status=OrderStatus.EXPIRED,
                        quantity=order.quantity,
                        filled_qty=order.filled_qty,
                        price=order.price,
                    ),
                )
                continue
            self.settle_order(position, order, result)

    @staticmethod
    def _too_fresh(created_at: str) -> bool:
        """订单是否距创建过近（查询不到时不能贸然判 EXPIRED）"""
        if not created_at:
            return True  # 年龄未知（运行中刚下的内存行）→ 保守跳过
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return False
        return datetime.now(UTC) - created < timedelta(seconds=_MIN_QUERY_AGE_SECONDS)

    def refresh_pending_entries(self, position: GridPosition) -> None:
        """查询并了结未成交的入场单, 防止继续建仓"""
        for order in list(position.pending_entry_orders):
            result = self._trading.query(order.order_id, self._config.symbol)
            if result is None:
                self.settle_order(
                    position,
                    order,
                    OrderResult(
                        custom_id=order.order_id,
                        symbol=self._config.symbol,
                        side=order.side,
                        status=OrderStatus.EXPIRED,
                        quantity=order.quantity,
                        filled_qty=order.filled_qty,
                        price=order.price,
                    ),
                )
                continue
            self.settle_order(position, order, result)
            if result.status.is_open():
                refreshed = self.cancel_and_refresh(order.order_id)
                if refreshed is not None:
                    self.settle_order(position, order, refreshed)

    def settle_pending_entries(self, position: GridPosition) -> bool:
        """了结未成交入场单（撤单 + 结算）, 返回仓位是否归零可归档"""
        self.refresh_pending_entries(position)
        return position.quantity <= 0

    def settle_exit(
        self, position: GridPosition, order: OrderRecord, result: OrderResult
    ) -> bool:
        """结算出场单成交回报, 返回仓位是否归零可归档"""
        self.settle_order(position, order, result)
        return position.quantity <= 0

    def force_settle_exit(self, position: GridPosition) -> bool:
        """强制出场: 撤掉休息中的出场挂单并结算已成交部分, 返回是否仍有可平量"""
        order = position.pending_exit_order
        if order is None:
            return position.quantity > 0
        refreshed = self.cancel_and_refresh(order.order_id)
        if refreshed is None:
            self.discard_order(position, order.order_id)
        else:
            self.settle_order(position, order, refreshed)
        return position.quantity > 0

    def track_pending_exits(self) -> tuple[list[GridPosition], bool]:
        """
        每根完成K线无条件跟踪已挂出的出场单

        - 未成交(open): 保留原挂单, 继续跟踪;
        - 确认成交(closed): 结算成交量, 剩余耗尽才算平仓完成;
        - 作废(canceled/rejected/expired): 结算后退回持仓, 等待重挂;
        - 查询不到: 先撤单复核, 确认订单不存在才删行退回持仓;
        - 查询异常: 保留出场单, 下一根再试, 绝不重复下单。

        @return: (可归档的仓位, 状态是否发生变化)
        """
        archived: list[GridPosition] = []
        changed = False
        for position in self._positions():
            order = position.pending_exit_order
            if order is None:
                continue

            try:
                result = self._trading.query(order.order_id, self._config.symbol)
            except SmartTraderError:
                logger.warning("查询出场单失败, 保留待跟踪: %s", order.order_id)
                continue

            if result is None:
                # 订单可能只是新单索引延迟, 撤单复核后再判定是否存在
                result = self.cancel_and_refresh(order.order_id)
                if result is None:
                    logger.warning("出场单不存在, 退回持仓: %s", order.order_id)
                    self.discard_order(position, order.order_id)
                    changed = True
                    continue

            if result.status.is_open():
                # 保留原挂单, 不需要重复挂
                continue

            changed = True
            if self.settle_exit(position, order, result):
                archived.append(position)

        return archived, changed
