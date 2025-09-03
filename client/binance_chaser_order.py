import secrets
from client.ex_client import ExSwapClient

import asyncio
import websockets
import json
import time
import ssl
from decimal import Decimal
from model import OrderStatus, Symbol
from model import OrderSide
import log

logger = log.getLogger(__name__)

class LimitOrderChaser:
    def __init__(self, client: ExSwapClient, symbol: Symbol, side: OrderSide, quantity: float, 
                 tick_size: float, position_side: str = "LONG"):
        print(f"symbol:{symbol}, side:{side}, quantity:{quantity}, position_side:{position_side}")
        self.client = client
        self.symbol: Symbol = symbol
        self.side = side
        self.quantity = quantity
        self.position_side = position_side.upper()
        self.ssl_context = ssl._create_unverified_context()
        self.order = None
        self.tick_size: float = tick_size
        self.price_precision = len(str(Decimal(str(self.tick_size))).split('.')[1])

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
        logger.debug("查询订单：%s", order_id)
        result = self.client.query_order(order_id, self.symbol)
        logger.debug("查询订单返回：%s", result)
        return result

    def cancel_order(self, order_id):
        logger.debug("撤单：%s", order_id)
        result = self.client.cancel(order_id, self.symbol)
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
        try:
            best_bid = latest_price - self.tick_size
            best_ask = latest_price + self.tick_size
            logger.info(f"最新价：{latest_price}, 最优买价: {best_bid}, 最优卖价: {best_ask}")
            limit_price = best_bid if self.side == OrderSide.BUY else best_ask
            
            if self.order:
                try:
                    latest_order = self.query_order(self.order['clientOrderId'])
                    if not latest_order:
                        logger.error("查询订单失败，订单可能已被删除")
                        self.order = None
                        return False
                        
                    if latest_order['status'] == OrderStatus.CLOSED.value:
                        logger.info(f"订单 {self.order['clientOrderId']} 已成交")
                        return True
                    elif latest_order['status'] in [OrderStatus.CANCELED.value, OrderStatus.REJECTED.value, OrderStatus.EXPIRED.value]:
                        logger.info(f"订单 {self.order['clientOrderId']} 已取消，重新下单")
                        self.order = None
                    elif latest_order['status'] == OrderStatus.OPEN.value:
                        latest_order_price = latest_order['price']
                        price_diff = abs(latest_order_price - limit_price)
                        price_more_competitive = price_diff > self.tick_size * 3
                        
                        if price_more_competitive:
                            logger.info(f"撤销订单 {self.order['clientOrderId']}，价格: {latest_order_price}, 重置价格: {limit_price}")
                            cancel_result = self.cancel_order(self.order['clientOrderId'])
                            if cancel_result:
                                if cancel_result['status'] == OrderStatus.CANCELED.value:
                                    self.order = None
                                elif cancel_result['status'] == OrderStatus.CLOSED.value:
                                    return True
                            else:
                                logger.warning("撤单失败, cancel_order 返回 None")
                                return False
                        else:
                            return False
                    else:
                        logger.warning(f"未知订单状态: {latest_order['status']}")
                        return False
                except Exception as e:
                    logger.error(f"查询或处理订单时出错: {e}")
                    return False
            
            if not self.order:
                try:
                    order_result = self.place_order_gtx(limit_price)
                    if order_result and order_result.get('clientOrderId'):
                        self.order = order_result
                        logger.info(f"新订单已下单: {order_result['clientOrderId']}, 价格: {limit_price}")
                    else:
                        logger.error(f"下单失败: {order_result}")
                        return False
                except Exception as e:
                    logger.error(f"下单时出错: {e}")
                    return False
                    
            return False
            
        except Exception as e:
            logger.error(f"chase函数执行出错: {e}")
            return False


    async def start(self, update_interval=1):
        ws_url = f"wss://fstream.binance.com/ws/{self.symbol.binance().lower()}@miniTicker"
        try:
            async with websockets.connect(ws_url, ssl=self.ssl_context) as ws:
                counter = 0
                max_iterations = 100
                
                while counter < max_iterations:
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
                        # elif '"id"' in msg_str and '12' in msg_str:
                            # logger.info("订阅确认")
                        
                        counter += 1
                        if counter >= max_iterations:
                            logger.warning(f"达到最大迭代次数 {max_iterations}，停止追单")
                            if self.order:
                                self.cancel_order(self.order['clientOrderId'])
                            break
                            
                    except asyncio.TimeoutError:
                        logger.warning("WebSocket接收超时，重新尝试")
                        counter += 25
                        
        except Exception as e:
            logger.error(e)
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()
        
        return self.order and self.order['status'] == OrderStatus.CLOSED.value