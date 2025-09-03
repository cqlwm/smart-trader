from typing import List
import ccxt
from ccxt.base.types import OrderType

from client.ex_client import ExSwapClient, ExSpotClient

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
from model import Symbol
from strategy import OrderSide
import log

logger = log.getLogger('BinanceSwapClient')

def get_tick_size(symbol):
    """
    获取指定交易对的最小价格间隔(tickSize)
    
    Args:
        symbol (str): 交易对名称，例如 'BTCUSDT'
    
    Returns:
        float: 最小价格间隔，如果未找到则返回 None
    """
    # url = "https://api.binance.com/api/v3/exchangeInfo"
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    symbol = symbol.upper()

    if symbol in ['BTCUSDT', 'BTCUSDC']:
        return 0.1
    if symbol in ['BNBUSDT', 'BNBUSDC', 'SOLUSDT', 'SOLUSDC']:
        return 0.01
    if symbol in ['DOGEUSDT', 'DOGEUSDC']:
        return 0.00001
    
    try:
        # 发送请求获取exchangeInfo数据
        response = requests.get(url)
        data = response.json()
        
        # 遍历所有交易对信息
        for symbol_info in data['symbols']:
            if symbol_info['symbol'] == symbol:
                # 在filters中查找PRICE_FILTER
                for filter_info in symbol_info['filters']:
                    if filter_info['filterType'] == 'PRICE_FILTER':
                        return float(filter_info['tickSize'])
        
        return None
    
    except Exception as e:
        print(f"获取tickSize时发生错误: {str(e)}")
        return None


class LimitOrderChaser:
    def __init__(self, api_key, api_secret, symbol, side, quantity, position_side="LONG", is_test=False):
        print(f"symbol:{symbol}, side:{side}, quantity:{quantity}, position_side:{position_side}")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://testnet.binancefuture.com" if is_test else "https://fapi.binance.com"
        self.symbol = symbol.replace('/', '')
        self.side = side.upper()
        self.quantity = quantity
        self.position_side = position_side.upper()
        self.ssl_context = ssl._create_unverified_context()
        self.order = None
        self.tick_size = get_tick_size(self.symbol)
        self.price_precision = len(str(Decimal(str(self.tick_size))).split('.')[1])

    def sign_params(self, params):
        query_string = urlencode(params)
        signature = hmac.new(self.api_secret.encode('utf-8'),
                           query_string.encode('utf-8'),
                           hashlib.sha256).hexdigest()
        params['signature'] = signature
        return params

    def place_limit_order(self, price):
        path = "/fapi/v1/order"
        params = {
            "symbol": self.symbol,
            "side": self.side,
            "type": "LIMIT",
            "timeInForce": "GTX",
            "quantity": self.quantity,
            "price": float(f"{Decimal(price):.{self.price_precision}f}"),
            "positionSide": self.position_side,
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }
        params = self.sign_params(params)
        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.post(self.base_url + path, params=params, headers=headers)
        result = response.json()
        print("下单返回：", result)
        return result

    def query_order(self, order_id):
        path = "/fapi/v1/order"
        params = {
            "symbol": self.symbol,
            "orderId": order_id,
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }
        params = self.sign_params(params)
        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.get(self.base_url + path, params=params, headers=headers)
        return response.json()

    def cancel_order(self, order_id):
        path = "/fapi/v1/order"
        params = {
            "symbol": self.symbol,
            "orderId": order_id,
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }
        params = self.sign_params(params)
        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.delete(self.base_url + path, params=params, headers=headers)
        result = response.json()
        print("撤单返回：", result)
        return result

    async def start(self, update_interval=1):
        async with websockets.connect("wss://fstream.binance.com/ws", ssl=self.ssl_context) as ws:
            subscribe_message = {
                "method": "SUBSCRIBE",
                "params": [f"{self.symbol.lower()}@miniTicker"],
                "id": 12
            }
            await ws.send(json.dumps(subscribe_message))
            print(f"已订阅 {self.symbol} 的最优挂单价格更新")

            counter = 0
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if 'c' in data and data['e'] == '24hrMiniTicker':
                        best_bid = float(data['c']) - self.tick_size
                        best_ask = float(data['c']) + self.tick_size

                        print(f"最优买单价: {best_bid}, 最优卖单价: {best_ask}")
                    else:
                        print('无效数据')
                        await asyncio.sleep(update_interval)
                        continue

                    target_price = best_bid if self.side.upper() == "BUY" else best_ask
                    print(f"当前目标价：{target_price}")

                    if self.order:
                        current_order_price = float(self.order.get('price', 0))
                        print(f"当前挂单价格：{current_order_price}, 价差：{abs(current_order_price - target_price)}")

                        self.order = self.query_order(self.order['orderId'])
                        print(f"订单状态：{self.order['status']}")
                        
                        if self.order.get("status") == "FILLED":
                            print(f"订单 {self.order['orderId']} 已完成，退出循环。")
                            break

                        try:
                            # 判断新价格是否更容易成交
                            price_more_competitive = abs(current_order_price - target_price) > self.tick_size * 3
                            if self.order.get("status") == "NEW" and price_more_competitive:
                                print(f"发现更优价格，撤销订单 {self.order['orderId']}")
                                self.cancel_order(self.order['orderId'])
                                self.order = None
                            
                            counter += 1
                            if counter > 100:
                                print(f"超过最大轮训次数，撤销订单 {self.order['orderId']}")
                                self.cancel_order(self.order['orderId'])
                                self.order = None
                                break

                        except Exception as e:
                            print(f"取消订单时出错：{e}")
                            await asyncio.sleep(update_interval)
                            continue

                    if not self.order:
                        formatted_price = f"{target_price}"
                        order_response = self.place_limit_order(formatted_price)
                        if order_response.get("status") == "CANCELED" or order_response.get('code'):
                            print("订单因市价触发被立即取消。")
                            self.order = None
                        else:
                            self.order = order_response
                            print(f"新挂单：方向 {self.side.upper()}，价格 {formatted_price}，持仓方向 {self.position_side}")
                        
                    await asyncio.sleep(update_interval)
                except Exception as e:
                    logger.exception(e)
                    await asyncio.sleep(update_interval)
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()

class BinanceSwapClient(ExSwapClient):
    def __init__(self, api_key, api_secret, is_test=False):
        self.exchange_name = 'binance'
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {
                'defaultType': 'future',
            }
        })
        self.exchange.set_sandbox_mode(is_test)
        self.create_chaser = lambda symbol, order_side, quantity, position_side: LimitOrderChaser(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            position_side=position_side,
            is_test=is_test
        )
            

    def balance(self, coin):
        balance = self.exchange.fetch_balance()
        return balance[coin.upper()]['free']

    def cancel(self, custom_id, symbol):
        return self.exchange.cancel_order(id='', symbol=symbol, params={
            'origClientOrderId': custom_id
        })

    #     'status': 'closed'
    def query_order(self, custom_id, symbol):
        order = self.exchange.fetch_order(id='', symbol=symbol, params={
            'origClientOrderId': custom_id
        })
        res = {'state': order['status']}
        return res

    def place_order(self, custom_id, symbol, order_side, position_side, quantity, price=None):
        if not price:
            order_chaser = self.create_chaser(symbol, order_side, quantity, position_side)
            order_chaser.run()
            if order_chaser.order:
                return order_chaser.order

        order_type: OrderType = 'limit' if price else 'market'
        order = self.exchange.create_order(symbol=symbol, type=order_type, side=order_side,
                                           amount=quantity, price=price,
                                           params={'newClientOrderId': custom_id, 'positionSide': position_side})
        return order

    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide, quantity: float, price=None, **kwargs):
        self.place_order(custom_id, symbol.binance(), order_side.name, kwargs['position_side'], quantity, price)

    def close_position(self, symbol, position_side, auto_cancel=True):
        positions = self.positions(symbol)
        for position in positions:
            if position['side'] == position_side:
                quantity = position['contracts']
                if quantity > 0:
                    order_side = 'sell' if position_side == 'long' else 'buy'
                    self.place_order(None, symbol, order_side, position_side, quantity)
        if auto_cancel:
            open_orders = self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                self.exchange.cancel_order(order['id'], symbol)

    def positions(self, symbol=None):
        return self.exchange.fetch_positions([symbol])

# 未测试
class BinanceSpotClient(ExSpotClient):
    def __init__(self, api_key, api_secret):
        self.client = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret
        })

    def balance(self, coin):
        balance = self.client.fetch_balance()
        return balance['total'][coin]

    def cancel(self, custom_id, symbol):
        return self.client.cancel_order(custom_id, symbol)

    def query_order(self, custom_id, symbol):
        return self.client.fetch_order(custom_id, symbol)

    def place_order(self, custom_id, symbol, order_side, quantity, price=None):
        order_type = 'limit' if price else 'market'
        order = self.client.create_order(symbol, order_type, order_side, quantity, price, {
            'newClientOrderId': custom_id
        })
        return order
