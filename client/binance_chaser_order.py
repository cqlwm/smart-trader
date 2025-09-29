import secrets
from client.ex_client import ExSwapClient

import asyncio
import websockets
import json
import ssl
from decimal import Decimal
from model import OrderStatus, PlaceOrderBehavior, Symbol
from model import OrderSide
import log

logger = log.getLogger(__name__)

class LimitOrderChaser:
    '''
    目前只针对Binance进行了适配
    '''
    def __init__(self, client: ExSwapClient, symbol: Symbol, side: OrderSide, quantity: float, tick_size: float, position_side: str = "LONG", place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER):
        logger.info(f"Init Chaser : {symbol.ccxt()}, {side.name}, {quantity}, {position_side}")
        self.client: ExSwapClient = client
        self.symbol: Symbol = symbol
        self.position_side: str = position_side.upper()
        self.side: OrderSide = side
        self.quantity: float = quantity
        self.place_order_behavior: PlaceOrderBehavior = place_order_behavior
        self.max_iterations: int = 40
        self.order = None
        self.chase_result = False

    def place_order_gtx(self, price: float):
        custom_id=f'{self.side.value}{secrets.token_hex(nbytes=5)}'
        logger.info("下单：%s, %s, %s, Qty: %s, Price: %s", custom_id, self.symbol.ccxt(), self.side.name, self.quantity, price)
        result = self.client.place_order_v2(
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

    def query_order(self, order_id: str):
        try:
            result = self.client.query_order(order_id, self.symbol)
        except Exception as _:
            logger.error(f"查询订单时出错: {order_id}", exc_info=True)
            return None
        logger.debug("查询订单返回：%s", result)
        return result

    def cancel_order(self, order_id: str):
        '''
        ccxt.base.errors.OrderNotFound: binance {"code":-2011,"msg":"Unknown order sent."}
        '''
        try:
            result = self.client.cancel(order_id, self.symbol)
        except Exception as _:
            logger.error(f"撤单时出错: {order_id}", exc_info=True)
            return None
        logger.debug("撤单返回：%s", result)
        return result

    def chase_open_only(self, latest_price: float) -> bool:
        '''
        仅执行追单只下单模式
        @param latest_price: 最新价格
        '''
        symbol_info = self.client.symbol_info(self.symbol)
        tick_size = symbol_info.tick_size

        limit_price = (latest_price - tick_size) if self.side == OrderSide.BUY else (latest_price + tick_size)
        try:
            place_order_result = self.place_order_gtx(limit_price)
            if place_order_result and place_order_result.get('status'):
                self.order = place_order_result
                return place_order_result['status'] in [OrderStatus.OPEN.value, OrderStatus.CLOSED.value]
        except Exception as e:
            if '"code":-5022' in str(e.args):
                # {"code":-5022,"msg":"由于订单无法以挂单方式成交，此挂单将被拒绝，不会记录在订单历史记录中。"}
                logger.info("价格将触发市价, GTX限价订单自动取消")
            else:
                logger.error(f"下单时出错, error: {str(e)}", exc_info=True)
        return False

    def chase_closed(self, latest_price: float):
        '''
        执行追逐限价单
        1. 使用买1价和卖1价最为限价单的价格
        2. 检查当前订单状态
            2.1 如果订单已成交，返回True
            2.2 如果订单已取消，重新下单
            2.3 如果订单未成交，检查最新价格是否更优
                2.3.1 如果价格更优，撤销旧订单
                2.3.2 检查是否撤销成功
                    2.3.2.1 如果撤销成功，重新下单
                    2.3.2.2 如果撤销失败，重新检查订单状态
                2.3.3 如果价格未更优，不做任何操作
        @param latest_price: 最新价格
        '''
        limit_price = (latest_price - self.tick_size) if self.side == OrderSide.BUY else (latest_price + self.tick_size)

        if self.order:
            query_order_result = self.query_order(self.order['clientOrderId'])
            if not query_order_result:
                self.order = None
                return False
                
            if query_order_result['status'] == OrderStatus.CLOSED.value:
                logger.info(f"订单 {self.order['clientOrderId']} 已成交")
                self.order = query_order_result
                return True
            
            if query_order_result['status'] in [OrderStatus.CANCELED.value, OrderStatus.REJECTED.value, OrderStatus.EXPIRED.value]:
                logger.info(f"订单 {self.order['clientOrderId']} 已取消")
                self.order = None
                return False

            if query_order_result['status'] == OrderStatus.OPEN.value:
                if float(query_order_result['info']['executedQty']) > 0:
                    # TODO 订单部分成交就认为是已成交
                    self.order = query_order_result
                    return True
                
                if abs(query_order_result['price'] - limit_price) > self.tick_size * 3:
                    logger.info(f"撤销订单 {self.order['clientOrderId']}，订单价格: {query_order_result['price']}, 重置订单价格: {limit_price}")
                    cancel_result = self.cancel_order(self.order['clientOrderId'])
                    if cancel_result and cancel_result['status'] == OrderStatus.CANCELED.value:
                        # 订单取消成功重置order；如果失败，下一轮查询确认状态
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
                self.cancel_order(self.order['clientOrderId'])
                return self.chase_closed(self.order['price'])
            else:
                return False

    def chase(self, latest_price: float) -> bool:
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
                            self.chase_result = self.chase(current_price)
                            if self.chase_result and self.order:
                                logger.info(f"结束追单, 订单 {self.order['clientOrderId']} {'已挂单' if self.place_order_behavior == PlaceOrderBehavior.CHASER_OPEN else '已成交'}")
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
