import secrets
from client.ex_client import ExSwapClient

import asyncio
import websockets
import json
import ssl
from model import Order, OrderStatus, PlaceOrderBehavior, Symbol
from model import OrderSide
import log

logger = log.getLogger(__name__)

class LimitOrderChaser:
    '''
    目前只针对Binance进行了适配
    '''
    def __init__(self, client: ExSwapClient, symbol: Symbol, side: OrderSide, quantity: float, strategy_id: str = "", position_side: str = "LONG", place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER):
        logger.info(f"Init Chaser : {symbol.ccxt()}, {side.name}, {quantity}, {position_side}")
        self.client: ExSwapClient = client
        self.symbol: Symbol = symbol
        self.strategy_id: str = strategy_id
        self.position_side: str = position_side.upper()
        self.side: OrderSide = side
        self.quantity: float = quantity
        self.place_order_behavior: PlaceOrderBehavior = place_order_behavior
        self.max_iterations: int = 40
        self.order: Order | None = None
        self.chase_result = False
        self.first_price: float | None = None
        self.fee: float = 0.002

    def place_order_gtx(self, price: float) -> Order | None:
        custom_id=f'{self.side.value}{secrets.token_hex(nbytes=5)}'
        logger.info("下单：%s, %s, %s, Qty: %s, Price: %s", custom_id, self.symbol.ccxt(), self.side.name, self.quantity, price)
        result = self.client.place_order_v2(
            strategy_id=self.strategy_id,
            custom_id=custom_id,
            symbol=self.symbol,
            order_side=self.side,
            quantity=self.quantity,
            price=price,
            position_side=self.position_side,
            time_in_force="GTX",
        )
        logger.debug("下单返回：%s", result)
        return result

    def query_order(self, order_id: str) -> Order | None:
        try:
            result = self.client.query_order(order_id, self.symbol)
        except Exception:
            logger.error(f"查询订单时出错: {order_id}", exc_info=True)
            return None
        logger.debug("查询订单返回：%s", result)
        return result

    def cancel_order(self, order_id: str) -> Order | None:
        try:
            result = self.client.cancel(order_id, self.symbol)
        except Exception:
            logger.error(f"撤单时出错: {order_id}", exc_info=True)
            return None
        logger.debug("撤单返回：%s", result)
        return result

    def chase_open_only(self, latest_price: float) -> bool:
        symbol_info = self.client.symbol_info(self.symbol)
        tick_size = symbol_info.tick_size

        limit_price = (latest_price - tick_size) if self.side == OrderSide.BUY else (latest_price + tick_size)
        if self.first_price is not None:
            if self.side == OrderSide.BUY:
                limit_price = min(limit_price, self.first_price)
            elif self.side == OrderSide.SELL:
                limit_price = max(limit_price, self.first_price)

        try:
            place_order_result = self.place_order_gtx(limit_price)
            if place_order_result and place_order_result.status:
                self.order = place_order_result
                return place_order_result.status in [OrderStatus.OPEN, OrderStatus.CLOSED]
        except Exception as e:
            if '"code":-5022' in str(e.args):
                logger.info("价格将触发市价, GTX限价订单自动取消")
            else:
                logger.error(f"下单时出错, error: {str(e)}", exc_info=True)
        return False

    def chase_closed(self, latest_price: float) -> bool:
        if self.order:
            query_order_result = self.query_order(self.order.order_id)
            if not query_order_result:
                self.order = None
                return False

            if query_order_result.status == OrderStatus.CLOSED:
                logger.info(f"订单 {self.order.order_id} 已成交")
                self.order = query_order_result
                return True

            if query_order_result.status in [OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED]:
                logger.info(f"订单 {self.order.order_id} 已取消")
                self.order = None
                return False

            if query_order_result.status == OrderStatus.OPEN:
                if query_order_result.filled_quantity > 0:
                    self.order = query_order_result
                    return True

                symbol_info = self.client.symbol_info(self.symbol)
                tick_size = symbol_info.tick_size
                limit_price = (latest_price - tick_size) if self.side == OrderSide.BUY else (latest_price + tick_size)
                order_price = query_order_result.price or 0
                if abs(order_price - limit_price) > tick_size * 3:
                    logger.info(f"撤销订单 {self.order.order_id}，订单价格: {order_price}, 重置订单价格: {limit_price}")
                    cancel_result = self.cancel_order(self.order.order_id)
                    if cancel_result and cancel_result.status == OrderStatus.CANCELED:
                        self.order = None
                return False
        else:
            self.chase_open_only(latest_price)

        return False

    def end_check(self) -> bool:
        if self.chase_result:
            return True
        else:
            if self.order:
                self.cancel_order(self.order.order_id)
                return self.chase_closed(self.order.price or 0)
            else:
                return False

    def chase(self, latest_price: float) -> bool:
        if self.first_price:
            return self.chase_open_only(self.first_price) and self.place_order_behavior == PlaceOrderBehavior.CHASER_OPEN

        if self.place_order_behavior == PlaceOrderBehavior.CHASER_OPEN:
            return self.chase_open_only(latest_price)
        elif self.place_order_behavior == PlaceOrderBehavior.CHASER:
            return self.chase_closed(latest_price)
        else:
            raise ValueError(f"未知的追逐下单行为 {self.place_order_behavior}")

    async def start(self):
        ws_url = f"wss://fstream.binance.com/ws/{self.symbol.binance().lower()}@miniTicker"
        counter = 0
        while True:
            async with websockets.connect(ws_url, ssl=ssl._create_unverified_context()) as ws: # type: ignore
                try:
                    await ws.ping()
                    while counter < self.max_iterations:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        msg_str = str(msg)
                        if '"c"' in msg_str and '24hrMiniTicker' in msg_str:
                            data = json.loads(msg)
                            current_price = float(data['c'])
                            if self.first_price is not None:
                                deviation = abs(current_price - self.first_price)
                                fee_range = self.fee * self.first_price
                                if deviation > fee_range:
                                    profitable = (self.side == OrderSide.BUY and current_price < self.first_price) or (self.side == OrderSide.SELL and current_price > self.first_price)
                                    if profitable:
                                        logger.info(f"价格偏离超出fee范围且盈利，停止追单。当前价: {current_price}, 初始价: {self.first_price}")
                                        self.chase_result = False
                                        counter = self.max_iterations
                                        break
                            self.chase_result = self.chase(current_price)
                            if self.chase_result and self.order:
                                logger.info(f"结束追单, 订单 {self.order.order_id} {'已挂单' if self.place_order_behavior == PlaceOrderBehavior.CHASER_OPEN else '已成交'}")
                                break

                            await asyncio.sleep(1)
                        counter += 1
                    logger.info(f"追单计数 {counter}")
                except asyncio.TimeoutError:
                    logger.warning("WebSocket接收超时, 重新尝试")
                    counter += 10
                except Exception as e:
                    logger.error(e)

            if counter >= self.max_iterations or self.chase_result:
                break
            else:
                logger.warning("WebSocket重连")

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()

        return self.end_check()
