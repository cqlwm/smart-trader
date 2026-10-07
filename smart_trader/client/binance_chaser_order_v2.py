import secrets
import time
from typing import Any, Protocol

from ccxt.base.errors import InvalidOrder, OrderNotFillable

from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.exceptions import SmartTraderError
from smart_trader.model import OrderSide, OrderStatus, PlaceOrderBehavior, Symbol

logger = log.getLogger(__name__)


class LatestPriceProvider(Protocol):
    """
    最新价来源，返回最新成交价
    """

    def latest_price(self, symbol: Symbol) -> float: ...


class LimitOrderChaserV2:
    """
    目前只针对Binance进行了适配
    不自建 websocket 连接，最新价由构造时注入的 price_provider 提供
    """

    def __init__(
        self,
        client: ExSwapClient,
        price_provider: LatestPriceProvider,
        symbol: Symbol,
        side: OrderSide,
        quantity: float,
        position_side: str = "LONG",
        place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER,
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
            position_side,
        )
        self.client: ExSwapClient = client
        self.price_provider: LatestPriceProvider = price_provider
        self.symbol: Symbol = symbol
        self.position_side: str = position_side.upper()
        self.side: OrderSide = side
        self.quantity: float = quantity
        self.place_order_behavior: PlaceOrderBehavior = place_order_behavior
        self.max_iterations: int = max_iterations
        self.max_retry: int = max_retry
        self.poll_interval: float = poll_interval
        self.wait_timeout: float = wait_timeout
        self.custom_id: str | None = None
        self.chase_result: bool = False
        self.first_price: float | None = None

    def place_order_gtx(self, limit_price: float) -> str | None:
        """
        按 limit_price 挂 GTX 限价单，成功返回 custom_id
        @param limit_price: 挂单价
        """
        custom_id = f"{self.side.value}{secrets.token_hex(nbytes=5)}"
        logger.info(
            "下单：%s, %s, %s, Qty: %s, Price: %s",
            custom_id,
            self.symbol.ccxt(),
            self.side.name,
            self.quantity,
            limit_price,
        )
        try:
            result = self.client.place_order_v2(
                custom_id=custom_id,
                symbol=self.symbol,
                order_side=self.side,
                quantity=self.quantity,
                price=limit_price,
                position_side=self.position_side,
                time_in_force="GTX",
            )
        except OrderNotFillable:
            # {"code":-5022,"msg":"由于订单无法以挂单方式成交，此挂单将被拒绝，不会记录在订单历史记录中。"}
            logger.info("价格将触发市价, GTX限价订单自动取消")
            return None
        except (InvalidOrder, SmartTraderError) as e:
            # 名义价值/精度等确定性拒绝, 递进价格重试无意义
            logger.error("订单被拒绝, 停止追单: %s", e)
            raise
        except Exception:
            logger.exception("下单时出错")
            return None
        logger.debug("下单返回：%s", result)
        if result and result.get("status") in [
            OrderStatus.OPEN.value,
            OrderStatus.CLOSED.value,
        ]:
            return custom_id
        return None

    def query_order(self, custom_id: str) -> dict[str, Any] | None:
        try:
            result = self.client.query_order(custom_id, self.symbol)
        except Exception as _:
            logger.exception("查询订单时出错: %s", custom_id)
            return None
        logger.debug("查询订单返回：%s", result)
        return result

    def cancel_order(self, custom_id: str) -> dict[str, Any] | None:
        """
        ccxt.base.errors.OrderNotFound: binance {"code":-2011,"msg":"Unknown order sent."}
        """
        try:
            result = self.client.cancel(custom_id, self.symbol)
        except Exception as _:
            logger.exception("撤单时出错: %s", custom_id)
            return None
        logger.debug("撤单返回：%s", result)
        return result

    def chase_open_only(self, limit_price: float) -> str | None:
        """
        使用 limit_price 挂单，失败则向更优方向逐档递进，直到成功或达到 max_retry
        @param limit_price: 起始挂单价
        @return: 挂单成功返回 custom_id，否则 None
        """
        tick_size = self.client.symbol_info(self.symbol).tick_size
        step = -tick_size if self.side == OrderSide.BUY else tick_size
        for _ in range(self.max_retry):
            custom_id = self.place_order_gtx(limit_price)
            if custom_id is not None:
                return custom_id
            limit_price += step

        logger.warning("递进 %d 次仍未挂单成功", self.max_retry)
        return None

    def chase_closed(self, custom_id: str) -> bool:
        """
        等待并校验订单成交，超时未成交则撤单，撤单后再校验一次
        @param custom_id: 订单id
        """
        deadline = time.monotonic() + self.wait_timeout
        while time.monotonic() < deadline:
            order = self.query_order(custom_id)
            if order:
                if float(order["info"]["executedQty"]) > 0:
                    logger.info("订单 %s 已成交", custom_id)
                    return True
                if order["status"] in [
                    OrderStatus.CANCELED.value,
                    OrderStatus.REJECTED.value,
                    OrderStatus.EXPIRED.value,
                ]:
                    logger.info("订单 %s 已取消", custom_id)
                    return False
            time.sleep(self.poll_interval)

        logger.info("订单 %s 等待 %ss 未成交, 撤单", custom_id, self.wait_timeout)
        self.cancel_order(custom_id)
        order = self.query_order(custom_id)
        return bool(order) and float(order["info"]["executedQty"]) > 0

    def chase(self, price: float) -> bool:
        custom_id = self.chase_open_only(price)
        if custom_id is None:
            return False

        self.custom_id = custom_id
        if self.place_order_behavior == PlaceOrderBehavior.CHASER:
            return self.chase_closed(custom_id)
        return True

    def start(self) -> None:
        for i in range(self.max_iterations):
            if i == 0 and self.first_price is not None:
                price: float = self.first_price
            else:
                price = self.price_provider.latest_price(self.symbol)

            self.chase_result = self.chase(price)
            if self.chase_result:
                logger.info("第 %d 轮追单结束, custom_id: %s", i + 1, self.custom_id)
                return

        logger.warning("追单 %d 轮仍未成功", self.max_iterations)

    def run(self) -> bool:
        self.start()
        return self.chase_result
