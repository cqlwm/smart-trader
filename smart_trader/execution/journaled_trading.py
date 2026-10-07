"""记账下单包装

为每次下单预留订单号并记录结果, 保证"交易所动作"与"本地账本"最终一致:

- 下单前先写 id 表（used=0 + 意图快照）;
- 挂上成功: 同一事务写入订单行 + 核销;
- 交易所明确拒绝: 直接核销;
- 结果未知(网络中断/响应异常): 保持 used=0, 交给对账器。

所有下单路径(策略常规单、追单的每次尝试)都必须经过本包装, 否则最终一致性失效。
"""

from smart_trader import log
from smart_trader.client.ports import TradingPort
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import OrderRequest, OrderResult, Symbol
from smart_trader.storage.order_journal import (
    IntentKind,
    OrderIntent,
    OrderJournal,
    OrderRow,
)

logger = log.getLogger(__name__)

# 明确"确定未成交"的错误码: 交易所拒绝/本地校验失败/限频, 订单号可以直接核销。
# 其余错误(网络中断、响应无法解析等)结果未知——请求可能已经到达交易所,
# 必须保留 used=0, 留给对账器确认, 否则幽灵订单永远找不回。
_SETTLED_ERROR_CODES = frozenset(
    {
        ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY,
        ErrorCode.ORDER_REJECTED,
        ErrorCode.INSUFFICIENT_FUNDS,
        ErrorCode.ORDER_NOTIONAL_TOO_SMALL,
        ErrorCode.ORDER_QUANTITY_TOO_SMALL,
        ErrorCode.EXCHANGE_RATE_LIMITED,
        ErrorCode.ORDER_NOT_FOUND,
    }
)


class JournaledTradingPort:
    """
    TradingPort 装饰器: place 预留/确认/核销, query/cancel 透传

    intent 是"本次下单动作"的意图快照（由策略层构造）;
    追单场景中同一次下单动作的多轮尝试共享同一个 intent。
    """

    def __init__(
        self,
        trading: TradingPort,
        journal: OrderJournal,
        strategy_key: str,
        intent: OrderIntent,
    ):
        self.trading = trading
        self.journal = journal
        self.strategy_key = strategy_key
        self.intent = intent

    def place(self, request: OrderRequest) -> OrderResult:
        custom_id = self.journal.reserve(
            self.strategy_key,
            self.intent,
            preferred_id=request.custom_id or None,
        )
        journaled_request = request.model_copy(update={"custom_id": custom_id})
        logger.info(
            "记账下单: %s, %s, %s, Qty: %s, Price: %s",
            custom_id,
            request.symbol.ccxt(),
            request.side.name,
            request.quantity,
            request.price,
        )

        try:
            result = self.trading.place(journaled_request)
        except SmartTraderError as e:
            if e.error_code in _SETTLED_ERROR_CODES:
                self.journal.void(custom_id)
            else:
                logger.warning(
                    "下单结果未知, 保留待对账: %s (%s)",
                    custom_id,
                    e.error_code.value,
                )
            raise
        except Exception:
            logger.exception("下单遭遇未预期异常, 保留待对账: %s", custom_id)
            raise

        if result.status.is_open() or result.status.is_closed():
            self._confirm(custom_id, journaled_request, result)
        else:
            # 交易所明确回 rejected/expired/canceled: 没有挂上
            self.journal.void(custom_id)

        return result

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return self.trading.query(custom_id, symbol)

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return self.trading.cancel(custom_id, symbol)

    def _confirm(
        self, custom_id: str, request: OrderRequest, result: OrderResult
    ) -> None:
        if self.intent.kind is IntentKind.ENTRY:
            self.journal.confirm(custom_id, self._build_entry_row(request, result))
            return

        exit_price = result.price if result.price is not None else request.price
        self.journal.confirm_exit(
            custom_id,
            self.intent.entry_ids,
            exit_id=result.custom_id,
            exit_price=exit_price or 0.0,
        )

    def _build_entry_row(self, request: OrderRequest, result: OrderResult) -> OrderRow:
        template = self.intent.entry_order
        if template is None:
            raise ValueError("ENTRY 意图缺少 entry_order 快照, 无法重建订单行")
        return template.model_copy(
            update={
                "entry_id": result.custom_id,
                "strategy_key": self.strategy_key,
                "price": (
                    result.price
                    if result.price is not None
                    else (request.price if request.price is not None else template.price)
                ),
                "status": result.status,
                "filled_quantity": result.filled_qty,
            }
        )
