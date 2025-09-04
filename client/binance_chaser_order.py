import secrets
from client.ex_client import ExSwapClient

import asyncio
import websockets
import json
import ssl
from decimal import Decimal
from model import OrderStatus, Symbol
from model import OrderSide
import log

logger = log.getLogger(__name__)

class LimitOrderChaser:
    def __init__(self, client: ExSwapClient, symbol: Symbol, side: OrderSide, quantity: float, 
                 tick_size: float, position_side: str = "LONG"):
        logger.info(f"symbol:{symbol}, side:{side}, quantity:{quantity}, position_side:{position_side}")
        self.client = client
        self.symbol: Symbol = symbol
        self.side = side
        self.quantity = quantity
        self.position_side = position_side.upper()
        self.ssl_context = ssl._create_unverified_context()
        self.order = None
        self.tick_size: float = tick_size
        self.price_precision = len(str(Decimal(str(self.tick_size))).split('.')[1])
        self.max_iterations = 40

    def place_order_gtx(self, price):
        custom_id=f'{self.side.value}{secrets.token_hex(nbytes=5)}'
        logger.info("下单：%s, %s, %s, %s, %s", self.symbol.ccxt(), self.side, self.quantity, price, custom_id)
        result = self.client.place_order_v2(
            custom_id=custom_id,
            symbol=self.symbol,
            order_side=self.side,
            quantity=self.quantity,
            price=float(f"{Decimal(price):.{self.price_precision}f}"),
            position_side=self.position_side,
            time_in_force="GTX",
        )
        logger.debug("下单返回：%s", result)
        return result

    def query_order(self, order_id):
        try:
            result = self.client.query_order(order_id, self.symbol)
        except Exception as _:
            logger.error(f"查询订单时出错: {order_id}", exc_info=True)
            return None
        logger.debug("查询订单返回：%s", result)
        return result

    def cancel_order(self, order_id):
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

    def chase(self, latest_price: float):
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
        '''
        best_bid = latest_price - self.tick_size
        best_ask = latest_price + self.tick_size
        limit_price = best_bid if self.side == OrderSide.BUY else best_ask
        
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
                    # 订单只要部分成交就认为是已成交
                    query_order_result['status'] = OrderStatus.CLOSED.value
                    self.order = query_order_result
                    return True
                
                if abs(query_order_result['price'] - limit_price) > self.tick_size * 3:
                    logger.info(f"撤销订单 {self.order['clientOrderId']}，订单价格: {query_order_result['price']}, 重置订单价格: {limit_price}")
                    cancel_result = self.cancel_order(self.order['clientOrderId'])
                    if cancel_result and cancel_result['status'] == OrderStatus.CANCELED.value:
                        # 订单取消成功重置order；如果失败，下一轮插叙确认状态
                        self.order = None
                return False
        else:
            try:
                place_order_result = self.place_order_gtx(limit_price)
                if place_order_result and place_order_result.get('status'):
                    self.order = place_order_result
                    logger.info(f"新订单已下单: {place_order_result['clientOrderId']}, 价格: {limit_price}")
                    return place_order_result['status'] == OrderStatus.CLOSED.value
            except Exception as e:
                if '"code":-5022' in str(e.args):
                    # {"code":-5022,"msg":"由于订单无法以挂单方式成交，此挂单将被拒绝，不会记录在订单历史记录中。"}
                    logger.info("价格将触发市价, GTX限价订单自动取消")
                else:
                    logger.error("下单时出错", exc_info=True)
                return False

        return False

    def end_check(self, is_end: bool = False):
        if self.order:
            if self.order['status'] == OrderStatus.CLOSED.value:
                return True
            if self.chase(self.order['price']):
                return True
            else:
                return is_end or self.end_check(is_end=True)
        else:
            return False

    async def start(self, update_interval=1):
        ws_url = f"wss://fstream.binance.com/ws/{self.symbol.binance().lower()}@miniTicker"
        async with websockets.connect(ws_url, ssl=self.ssl_context) as ws:
            counter = 0
            while counter < self.max_iterations:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg_str = str(msg)
                    if '"c"' in msg_str and '24hrMiniTicker' in msg_str:
                        data = json.loads(msg)
                        current_price = float(data['c'])
                        chase_result = self.chase(current_price)
                        if chase_result:
                            logger.info("订单已成交，退出追单循环")
                            break
                        await asyncio.sleep(update_interval)
                    
                    counter += 1
                    if counter >= self.max_iterations:
                        logger.warning(f"达到最大迭代次数 {self.max_iterations}，停止追单")
                        if self.order:
                            self.cancel_order(self.order['clientOrderId'])
                        break
                except asyncio.TimeoutError:
                    logger.warning("WebSocket接收超时, 重新尝试")
                    counter += 15
                except Exception as e:
                    logger.error(e)
                    
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()
        
        return self.end_check()
