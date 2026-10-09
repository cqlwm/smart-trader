"""追单执行策略

只依赖端口, 不依赖任何交易所 SDK:
以 GTX(post only)限价单起步, 被拒时向更容易成为 maker 的方向递进价格,
挂上后可选择等待成交, 超时则撤单。
"""

import time

from smart_trader import log
from smart_trader.client.ports import MarketDataPort, TradingPort
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
    TimeInForce,
)

logger = log.getLogger(__name__)


class LimitOrderChaser:
    """限价追单器: 争取以 maker 成交, 不自己维护行情连接"""

    def __init__(
        self,
        trading: TradingPort,
        market: MarketDataPort,
        symbol: Symbol,
        side: OrderSide,
        quantity: float,
        position_side: PositionSide,
        behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN,
        max_iterations: int = 40,
        max_retry: int = 10,
        poll_interval: float = 1.0,
        wait_timeout: float = 3.0,
    ):
        logger.info(
            "Init Chaser : %s, %s, %s, %s",
            symbol.ccxt(),
            side.name,
            quantity,
            position_side.value,
        )
        self.trading = trading
        self.market = market
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.position_side = position_side
        self.behavior = behavior
        self.max_iterations = max_iterations
        self.max_retry = max_retry
        self.poll_interval = poll_interval
        self.wait_timeout = wait_timeout

    def chase_open_only(self, limit_price: float) -> OrderResult | None:
        """
        使用 limit_price 挂单, 失败则向更优方向逐档递进, 直到成功或达到 max_retry
        @param limit_price: 起始挂单价
        @return: 挂单成功返回订单结果, 否则 None
        """
        tick_size = self.market.symbol_info(self.symbol).tick_size
        step = -tick_size if self.side == OrderSide.BUY else tick_size

        price = limit_price
        for _ in range(self.max_retry):
            result = self._place_gtx(price)
            if result is not None:
                return result
            price += step

        logger.warning("递进 %d 次仍未挂单成功", self.max_retry)
        return None

    def _place_gtx(self, price: float) -> OrderResult | None:
        """
        挂一张 GTX(post only)限价单

        订单号由记账包装分配并在多轮递进间复用（传空 custom_id）。

        @return: 挂单被交易所接受返回订单结果, 被拒返回 None
        @raise SmartTraderError: 确定性拒绝(精度/名义价值等)直接抛出
        """
        request = OrderRequest(
            custom_id="",
            symbol=self.symbol,
            side=self.side,
            type=OrderType.LIMIT,
            quantity=self.quantity,
            price=price,
            position_side=self.position_side,
            time_in_force=TimeInForce.GTX,
        )
        logger.info(
            "下单：%s, %s, Qty: %s, Price: %s",
            self.symbol.ccxt(),
            self.side.name,
            self.quantity,
            price,
        )
        try:
            result = self.trading.place(request)
        except SmartTraderError as e:
            if e.error_code == ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY:
                logger.info("价格将触发市价, GTX限价订单自动取消")
                return None
            if e.is_transient:
                logger.warning("下单遇到瞬时错误, 递进价格重试: %s", e)
                return None
            # 名义价值/精度等确定性拒绝, 递进价格重试无意义
            logger.error("订单被拒绝, 停止追单: %s", e)
            raise

        if result.status.is_finished() and not result.status.is_closed():
            # 交易所未抛异常但直接回 rejected/expired, 同样没有挂上单
            logger.info("订单未挂上, 状态: %s", result.status.value)
            return None
        return result

    def wait_for_fill(self, custom_id: str) -> OrderResult | None:
        """
        等待订单成交, 超时则撤单后再确认一次
        @param custom_id: 订单号
        @return: 有成交返回最后一次订单结果, 未成交返回 None
        """
        deadline = time.monotonic() + self.wait_timeout
        while time.monotonic() < deadline:
            order = self._query(custom_id)
            if order is not None and order.status.is_finished():
                # 只有部分成交时不能提前返回, 否则剩余挂单会留在交易所
                return order if order.filled_qty > 0 else None
            time.sleep(self.poll_interval)

        logger.info("订单 %s 等待 %ss 未成交, 撤单", custom_id, self.wait_timeout)
        self._cancel(custom_id)
        order = self._query(custom_id)
        if order is not None and order.filled_qty > 0:
            return order
        return None

    def _query(self, custom_id: str) -> OrderResult | None:
        try:
            result = self.trading.query(custom_id, self.symbol)
        except SmartTraderError:
            logger.exception("查询订单时出错: %s", custom_id)
            return None
        logger.debug("查询订单返回：%s", result)
        return result

    def _cancel(self, custom_id: str) -> None:
        try:
            self.trading.cancel(custom_id, self.symbol)
        except SmartTraderError:
            logger.exception("撤单时出错: %s", custom_id)

    def run(self, price: float, first_price: float | None = None) -> OrderResult | None:
        """
        执行一次追单: 挂单成功即返回; CHASER 行为会等待成交
        @param price: 最新价
        @param first_price: 第一档挂单价, 为空时使用 price
        """
        result = self.chase_open_only(first_price if first_price is not None else price)
        if result is None:
            return None

        if self.behavior == PlaceOrderBehavior.CHASER:
            return self.wait_for_fill(result.custom_id)
        return result

    def start(self, first_price: float | None = None) -> OrderResult | None:
        """
        多轮追单: 每轮取最新价重新尝试, 直到成功或达到 max_iterations
        """
        for i in range(self.max_iterations):
            if i == 0 and first_price is not None:
                price = first_price
            else:
                price = self.market.latest_price(self.symbol)

            result = self.run(price, first_price=price)
            if result is not None:
                logger.info("第 %d 轮追单结束, custom_id: %s", i + 1, result.custom_id)
                return result

        logger.warning("追单 %d 轮仍未成功", self.max_iterations)
        return None
