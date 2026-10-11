"""启动对账

扫描 `orders` 中 status 为空（结果未确认）的行, 去交易所确认:

- 订单存在（open / closed / 有任何成交）→ 用交易所事实回写该行;
- 查询不到 → 删除该行（交易所从未存在）;
- 查询失败 → 保留, 留给下一次对账;
- 距创建时间过近的记录先跳过（可能尚未在交易所 settle）。

单条记录的失败不会中断整体对账; 对账完成后策略再加载仓位（load_active_positions）。
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.exchange.ports import TradingPort
from smart_trader.model import OrderResult, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import OrderRecord

logger = log.getLogger(__name__)


class ReconcileSummary(BaseModel):
    """一次对账的统计结果"""

    model_config = ConfigDict(extra="forbid")

    recovered: int = 0
    settled: int = 0
    skipped: int = 0
    failed: int = 0


class OrderReconciler:
    """用未确认的订单行去交易所确认, 回写或删除"""

    def __init__(
        self,
        journal: OrderJournal,
        trading: TradingPort,
        strategy_key: str,
        min_age_seconds: float = 5.0,
    ):
        """
        @param journal: 订单日志
        @param trading: 交易端口（只使用 query）
        @param strategy_key: 对账归属策略
        @param min_age_seconds: 小于该年龄的记录跳过（防止干扰尚未 settle 的订单）
        """
        self.journal = journal
        self.trading = trading
        self.strategy_key = strategy_key
        self.min_age_seconds = min_age_seconds

    def reconcile(self) -> ReconcileSummary:
        summary = ReconcileSummary()
        for record in self.journal.pending_orders(self.strategy_key):
            if self._too_fresh(record.created_at):
                summary.skipped += 1
                continue

            try:
                order = self.trading.query(
                    record.order_id, Symbol.from_ccxt(record.symbol)
                )
            # 对账必须容忍单条查询的一切失败: 保留线索待下次, 不能中断整体
            except Exception as e:  # noqa: BLE001
                logger.warning("对账查询失败, 保留待下次: %s (%s)", record.order_id, e)
                summary.failed += 1
                continue

            if order is not None and self._has_live_order(order):
                self.journal.settle_order(self._settle_record(record, order))
                summary.recovered += 1
                logger.info(
                    "对账恢复订单: %s -> %s", record.order_id, order.status.value
                )
            else:
                self.journal.discard_order(record.order_id)
                summary.settled += 1
                logger.info("对账删除预写行(交易所无单): %s", record.order_id)

        if any(
            (summary.recovered, summary.settled, summary.skipped, summary.failed)
        ):
            logger.info(
                "对账完成: 恢复 %s, 删除 %s, 跳过 %s, 失败 %s",
                summary.recovered,
                summary.settled,
                summary.skipped,
                summary.failed,
            )
        return summary

    def _too_fresh(self, created_at: str) -> bool:
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return False
        return datetime.now(UTC) - created < timedelta(seconds=self.min_age_seconds)

    @staticmethod
    def _has_live_order(order: OrderResult) -> bool:
        """判断交易所中是否存在有效订单（有成交的终态同样必须恢复）"""
        if order.status.is_open() or order.status.is_closed():
            return True
        return order.filled_qty > 0

    @staticmethod
    def _settle_record(record: OrderRecord, order: OrderResult) -> OrderRecord:
        """用交易所事实回写预写行"""
        return record.model_copy(
            update={
                "price": order.price if order.price is not None else record.price,
                "quantity": order.quantity if order.quantity > 0 else record.quantity,
                "filled_qty": order.filled_qty,
                "avg_price": order.avg_price,
                "status": order.status,
            }
        )