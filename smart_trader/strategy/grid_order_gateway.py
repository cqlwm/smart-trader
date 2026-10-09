"""网格下单入口: 记账下单 + 追单降级, 保证交易所与账本最终一致"""

from collections.abc import Callable

from smart_trader import log
from smart_trader.client.ports import MarketDataPort, TradingPort
from smart_trader.execution.journaled_trading import JournaledTradingPort
from smart_trader.execution.limit_order_chaser import LimitOrderChaser
from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    PlaceOrderBehavior,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig

logger = log.getLogger(__name__)


class GridOrderGateway:
    """下单入口与内存订单记录构造

    依赖 config 的方向推导（position_reverse 后的 position_side）
    由策略在组装时以回调注入, 保持行为与拆分前一致。
    """

    def __init__(
        self,
        config: SignalGridStrategyConfig,
        trading: TradingPort,
        market: MarketDataPort,
        journal: OrderJournal,
        position_side: Callable[[], PositionSide],
    ):
        self._config = config
        self._trading = trading
        self._market = market
        self._journal = journal
        self._position_side = position_side

    def place_order(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        first_price: float | None = None,
        position: GridPositionRow | None = None,
        grid_position_id: str | None = None,
    ) -> OrderResult | None:
        """
        下单入口: 订单号由账本在发送前分发, 记账下单保证交易所与账本最终一致
        @param side: 买卖方向(已做反向处理)
        @param qty: 数量
        @param price: 价格
        @param first_price: 追单第一档价格
        @param position: 新仓位的策略参数模板（首单同事务建仓位）
        @param grid_position_id: 追加订单到已有仓位
        @return: 交易所返回的订单结果
        """
        request = OrderRequest(
            custom_id="",  # 订单号由账本在发送前分发, 保证先登记后下单
            symbol=self._config.symbol,
            side=side,
            type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            position_side=self._position_side(),
        )
        placer = JournaledTradingPort(
            trading=self._trading,
            journal=self._journal,
            strategy_key=self._config.strategy_key,
            position=position,
            grid_position_id=grid_position_id,
        )

        if self._config.place_order_behavior == PlaceOrderBehavior.NORMAL:
            return placer.place(request)

        chaser = LimitOrderChaser(
            trading=placer,
            market=self._market,
            symbol=self._config.symbol,
            side=side,
            quantity=qty,
            position_side=self._position_side(),
            behavior=self._config.place_order_behavior,
        )
        result = chaser.run(price, first_price=first_price)
        if result is not None:
            return result

        # 追单耗尽仍未挂上: 降级为常规限价单, 避免静默丢单
        logger.warning(
            "追单未成功, 降级为常规限价单: %s, %s, Qty: %s",
            self._config.symbol.ccxt(),
            side.name,
            qty,
        )
        return placer.place(request)

    def place_and_append(
        self,
        position: GridPosition,
        side: OrderSide,
        qty: float,
        price: float,
        first_price: float | None = None,
        template: GridPositionRow | None = None,
    ) -> OrderResult | None:
        """下单并把结果按账本预写行追加为仓位内存订单记录

        @param template: 新仓位模板（首单同事务建仓位）; 为空则追加到已有仓位
        """
        result = self.place_order(
            side,
            qty,
            price,
            first_price=first_price,
            position=template,
            grid_position_id=None if template is not None else position.id,
        )
        if result is not None:
            position.orders.append(self.order_record(position, qty, price, result))
        return result

    def order_record(
        self,
        position: GridPosition,
        qty: float,
        price: float,
        result: OrderResult,
    ) -> OrderRecord:
        """构造与账本预写行内容一致的内存订单记录"""
        return OrderRecord(
            order_id=result.custom_id,
            grid_position_id=position.id,
            strategy_key=self._config.strategy_key,
            symbol=self._config.symbol.ccxt(),
            side=result.side,
            type=OrderType.LIMIT,
            price=result.price if result.price is not None else price,
            quantity=qty,
            filled_qty=result.filled_qty,
            avg_price=result.avg_price,
            status=result.status,
        )
