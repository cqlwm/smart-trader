"""记账下单包装

一个实例代表**一次下单动作**（含追单多档），为每次下单预写订单行并回写结果,
保证"交易所动作"与"本地账本"最终一致:

- 下单前先写订单行（`status` 为空 = 结果未确认）; 新仓位的首单与仓位行同事务写入;
- 交易所从未存在的订单（GTX 被拒 -5022 等）: 删除预写行, 同一 `order_id` 复用重试;
- 结果未知（网络中断/响应异常）: 保持 `status` 为空, 交给对账器;
- 交易所明确回报（挂上 / 拒绝 / 取消 / 过期）: 用交易所事实回写, 该 `order_id` 作废。

所有下单路径(策略常规单、追单的每次尝试)都必须经过本包装, 否则最终一致性失效。
"""

from smart_trader import log
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import TradingPort
from smart_trader.model import OrderRequest, OrderResult, Symbol
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord

logger = log.getLogger(__name__)

# 明确"交易所从未存在该订单"的错误码: 拒绝/本地校验失败/限频, 预写行可直接删除,
# 且 order_id 可复用。其余错误(网络中断、响应无法解析等)结果未知——请求可能已经到达交易所,
# 必须保留 status 为空, 留给对账器确认, 否则幽灵订单永远找不回。
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


class JournaledTradingPort(TradingPort):
    """
    TradingPort 装饰器: place 预写/回写, query/cancel 透传

    二选一: `position`（新仓位的首单, 同事务建仓位）或 `grid_position_id`（向已有仓位追加订单）。
    """

    def __init__(
        self,
        trading: TradingPort,
        journal: OrderJournal,
        strategy_key: str,
        position: GridPositionRow | None = None,
        grid_position_id: str | None = None,
    ):
        if (position is None) == (grid_position_id is None):
            raise ValueError("必须且只能提供 position 或 grid_position_id 之一")
        if grid_position_id is not None and not grid_position_id:
            # 空串会原样落库成无主订单行: load_orders_for_positions 的 in_ 过滤
            # 永远查不到它, 交易所活单无法归因到仓位。仓位号缺失是调用方的错误,
            # 响亮拒绝, 不接受静默占位。
            raise ValueError("grid_position_id 不能为空字符串")
        self.trading = trading
        self.journal = journal
        self.strategy_key = strategy_key
        self._position = position
        self._grid_position_id = grid_position_id
        self._order_id: str | None = None
        self._row_written = False

    def place(self, request: OrderRequest) -> OrderResult:
        """
        @raise SmartTraderError: 发单前订单号未确定（账本无法关联交易所, 拒发）
        @note 不用 assert 校验不变量: python -O 会整段剥离, 且 AssertionError
            不在统一异常体系内, 策略层的 except SmartTraderError 捕不到
        """
        self._ensure_row(request)
        order_id = self._order_id
        if order_id is None:
            raise SmartTraderError(
                ErrorCode.ORDER_NOT_PLACED, "预写行缺少订单号, 无法向交易所发单"
            )
        journaled_request = request.model_copy(update={"custom_id": order_id})
        logger.info(
            "记账下单: %s, %s, %s, Qty: %s, Price: %s",
            order_id,
            request.symbol.ccxt(),
            request.side.name,
            request.quantity,
            request.price,
        )

        try:
            result = self.trading.place(journaled_request)
        except SmartTraderError as e:
            if e.error_code in _SETTLED_ERROR_CODES:
                # 交易所从未存在该订单: 删行, 保留 id 供复用
                self.journal.discard_order(order_id)
                self._row_written = False
            else:
                logger.warning(
                    "下单结果未知, 保留待对账: %s (%s)", order_id, e.error_code.value
                )
                self._invalidate_id()
            raise
        except Exception:
            logger.exception("下单遭遇未预期异常, 保留待对账: %s", order_id)
            self._invalidate_id()
            raise

        self.journal.settle_order(self._record_from_result(request, result))
        self._invalidate_id()
        return result

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return self.trading.query(custom_id, symbol)

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        return self.trading.cancel(custom_id, symbol)

    def _ensure_row(self, request: OrderRequest) -> None:
        """下单前确保订单行已写入（首单同事务建仓位）"""
        if self._row_written:
            return

        _order_id = self._order_id
        if _order_id is None:
            _order_id = self.journal.generate_order_id(request.side)
            self._order_id = _order_id

        if self._position is not None:
            # 首单: 仓位号此刻尚未落库, open_position 在同一事务内生成并写入。
            # 预写行的归属由那次插入决定, 此处的临时值随后即被覆盖。
            record = self._new_record(request, _order_id, self._position.id)
            position_id, order_id = self.journal.open_position(self._position, record)
            self._grid_position_id = position_id
            self._order_id = order_id
            self._position = None
        else:
            # 追加: 仓位已存在, 仓位号必为真实值（构造期已排除空串与 None）
            grid_position_id = self._grid_position_id
            if grid_position_id is None:
                raise SmartTraderError(
                    ErrorCode.ORDER_NOT_PLACED, "追加订单缺少仓位号, 无法归属仓位"
                )
            record = self._new_record(request, _order_id, grid_position_id)
            self._order_id = self.journal.append_order(record)
        self._row_written = True

    def _new_record(
        self, request: OrderRequest, order_id: str, grid_position_id: str
    ) -> OrderRecord:
        return OrderRecord(
            order_id=order_id,
            grid_position_id=grid_position_id,
            strategy_key=self.strategy_key,
            symbol=request.symbol.ccxt(),
            side=request.side,
            type=request.type,
            price=request.price,
            quantity=request.quantity,
        )

    def _invalidate_id(self) -> None:
        """订单已确认在交易所存在（或被接受）: 该 id 作废, 后续下单必须换新"""
        self._order_id = None
        self._row_written = False

    def _record_from_result(
        self, request: OrderRequest, result: OrderResult
    ) -> OrderRecord:
        """
        @raise SmartTraderError: 订单号或仓位号未确定（账本无法回写成交事实）
        @note 不用 assert: python -O 会整段剥离。错误的 order_id 会让
            settle_order 静默更新 0 行, 丢失成交事实
        """
        order_id = self._order_id
        grid_position_id = self._grid_position_id
        if order_id is None or grid_position_id is None:
            raise SmartTraderError(
                ErrorCode.ORDER_NOT_PLACED, "预写行标识缺失, 无法回写成交结果"
            )
        return OrderRecord(
            order_id=order_id,
            grid_position_id=grid_position_id,
            strategy_key=self.strategy_key,
            symbol=request.symbol.ccxt(),
            side=result.side,
            type=request.type,
            price=result.price if result.price is not None else request.price,
            quantity=request.quantity,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
            status=result.status,
        )