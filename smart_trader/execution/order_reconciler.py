"""启动对账

扫描 id 表中 used=0（结果未确认）的记录, 去交易所确认:

- 订单存在（含部分成交）→ 用「意图快照 + 交易所事实」补全 order 行并核销;
- 订单不存在或零成交终结 → 核销;
- 查询失败 → 保留, 留给下一次对账;
- 距预留时间过近的记录先跳过（可能尚未在交易所 settle）。

单条记录的失败不会中断整体对账; 对账完成后策略再加载仓位（load_positions）。
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.client.ports import TradingPort
from smart_trader.model import OrderResult
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import (
    IdRecord,
    OrderIntent,
    OrderRecord,
    OrderRole,
)

logger = log.getLogger(__name__)


class ReconcileSummary(BaseModel):
    """一次对账的统计结果"""

    model_config = ConfigDict(extra="forbid")

    recovered: int = 0
    settled: int = 0
    skipped: int = 0
    failed: int = 0


class OrderReconciler:
    """用未确认的订单号去交易所确认, 补全或核销"""

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
        for record in self.journal.pending_ids(self.strategy_key):
            if self._too_fresh(record.created_at):
                summary.skipped += 1
                continue

            try:
                order = self.trading.query(record.id, record.intent.symbol)
            # 对账必须容忍单条查询的一切失败: 保留线索待下次, 不能中断整体
            except Exception as e:  # noqa: BLE001
                logger.warning("对账查询失败, 保留待下次: %s (%s)", record.id, e)
                summary.failed += 1
                continue

            if order is not None and self._has_live_order(order):
                try:
                    self._recover(record, order)
                except ValueError:
                    logger.exception("对账数据异常, 保留待下次: %s", record.id)
                    summary.failed += 1
                    continue
                summary.recovered += 1
                logger.info("对账恢复订单: %s -> %s", record.id, order.status.value)
            else:
                self.journal.void(record.id)
                summary.settled += 1
                logger.info("对账核销订单号(交易所无单): %s", record.id)

        if any(
            (summary.recovered, summary.settled, summary.skipped, summary.failed)
        ):
            logger.info(
                "对账完成: 恢复 %s, 核销 %s, 跳过 %s, 失败 %s",
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

    def _recover(self, record: IdRecord, order: OrderResult) -> None:
        """用意图快照 + 交易所事实重建仓位与流水, 并核销"""
        intent = record.intent
        if intent.kind is OrderRole.ENTRY:
            template = intent.entry_position
            if template is None:
                raise ValueError(f"ENTRY 意图缺少 entry_position 快照: {record.id}")
            position = template.model_copy(
                update={
                    "entry_id": order.custom_id,
                    "strategy_key": self.strategy_key,
                    "price": (
                        order.price if order.price is not None else template.price
                    ),
                    "status": order.status,
                    "filled_quantity": order.filled_qty,
                }
            )
            self.journal.confirm_entry(
                record.id, position, self._record_from_query(intent, order)
            )
            return

        self.journal.confirm_exit(
            record.id,
            intent.entry_ids,
            exit_id=order.custom_id,
            exit_price=order.price if order.price is not None else 0.0,
            order=self._record_from_query(intent, order),
        )

    def _record_from_query(
        self, intent: OrderIntent, result: OrderResult
    ) -> OrderRecord:
        """从意图快照 + 交易所查询事实构造订单流水"""
        return OrderRecord(
            order_id=result.custom_id,
            strategy_key=self.strategy_key,
            role=intent.kind,
            symbol=intent.symbol.ccxt(),
            side=result.side if result.side is not None else intent.side,
            type=intent.order_type,
            price=result.price,
            quantity=result.quantity,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
            status=result.status,
        )
