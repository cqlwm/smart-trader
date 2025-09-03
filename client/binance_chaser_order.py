from typing import List
import ccxt
from ccxt.base.types import OrderType

from client.ex_client import ExClient, ExSwapClient, ExSpotClient

import asyncio
import websockets
import json
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
import ssl
from decimal import Decimal
from model import Kline, Symbol
from strategy import OrderSide
import log
from client.binance_client import get_tick_size

logger = log.getLogger(__name__)

class LimitOrderChaser:
    def __init__(self, client: ExSwapClient, symbol: Symbol, side: OrderSide, quantity: float, position_side: str = "LONG", is_test: bool = False):
        print(f"symbol:{symbol}, side:{side}, quantity:{quantity}, position_side:{position_side}")
        self.client = client
        self.base_url = "https://testnet.binancefuture.com" if is_test else "https://fapi.binance.com"
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.position_side = position_side.upper()
        self.ssl_context = ssl._create_unverified_context()
        self.order = None
        self.tick_size: float = get_tick_size(self.symbol) or 0.01
        self.price_precision = len(str(Decimal(str(self.tick_size))).split('.')[1])

    def place_limit_order(self, price):
        logger.info("下单：%s, %s, %s, %s", self.symbol, self.side, self.quantity, price)
        result = self.client.place_order_v2(
            custom_id=str(time.time()),
            symbol=self.symbol,
            order_side=self.side,
            quantity=self.quantity,
            price=float(f"{Decimal(price):.{self.price_precision}f}"),
            timeInForce="GTX",
        )
        logger.info("下单返回：%s", result)
        return result

    def query_order(self, order_id):
        logger.info("查询订单：%s", order_id)
        result = self.client.query_order(order_id, self.symbol)
        logger.info("查询订单返回：%s", result)
        return result

    def cancel_order(self, order_id):
        logger.info("撤单：%s", order_id)
        result = self.client.cancel(order_id, self.symbol)
        logger.info("撤单返回：%s", result)
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
                    latest_order = self.query_order(self.order['orderId'])
                    if not latest_order:
                        logger.error("查询订单失败，订单可能已被删除")
                        self.order = None
                        return False
                        
                    if latest_order['status'] == 'FILLED':
                        logger.info(f"订单 {self.order['orderId']} 已成交")
                        return True
                    elif latest_order['status'] == 'CANCELED':
                        logger.info(f"订单 {self.order['orderId']} 已取消，重新下单")
                        self.order = None
                    elif latest_order['status'] in ['NEW', 'PARTIALLY_FILLED']:
                        current_price = float(latest_order['price'])
                        price_diff = abs(current_price - limit_price)
                        price_more_competitive = price_diff > self.tick_size * 3
                        
                        if price_more_competitive:
                            logger.info(f"发现更优价格，撤销订单 {self.order['orderId']}，当前价格: {current_price}, 目标价格: {limit_price}")
                            cancel_result = self.cancel_order(self.order['orderId'])
                            if cancel_result:
                                self.order = None
                            else:
                                logger.warning("撤单失败，保持现有订单")
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
                    order_result = self.place_limit_order(limit_price)
                    if order_result and order_result.get('orderId'):
                        self.order = order_result
                        logger.info(f"新订单已下单: {order_result['orderId']}, 价格: {limit_price}")
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
        ws_url = "wss://fstream3.binance.com/ws" if not self.base_url.startswith("https://testnet") else "wss://fstream.binancefuture.com/ws"
        
        try:
            async with websockets.connect(ws_url, ssl=self.ssl_context) as ws:
                subscribe_message = {
                    "method": "SUBSCRIBE",
                    "params": [f"{str(self.symbol).lower()}@miniTicker"],
                    "id": 12
                }
                await ws.send(json.dumps(subscribe_message))
                logger.info(f"已订阅 {self.symbol} 的价格更新")

                counter = 0
                max_iterations = 1000
                
                while counter < max_iterations:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        data = json.loads(msg)
                        
                        if 'c' in data and data.get('e') == '24hrMiniTicker':
                            current_price = float(data['c'])
                            logger.debug(f"收到价格更新: {current_price}")
                            
                            chase_result = self.chase(current_price)
                            if chase_result:
                                logger.info("订单已成交，退出追单循环")
                                break
                        elif data.get('id') == 12:
                            logger.info("订阅确认")
                        
                        counter += 1
                        if counter >= max_iterations:
                            logger.warning(f"达到最大迭代次数 {max_iterations}，停止追单")
                            if self.order:
                                self.cancel_order(self.order['orderId'])
                            break
                            
                    except asyncio.TimeoutError:
                        logger.warning("WebSocket接收超时，重新尝试")
                        continue
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析错误: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"处理WebSocket消息时出错: {e}")
                        await asyncio.sleep(update_interval)
                        continue
                        
        except Exception as e:
            logger.error(f"WebSocket连接错误: {e}")
            raise
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()
