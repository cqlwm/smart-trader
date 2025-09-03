import ccxt
from ccxt.base.types import OrderType

from client.binance_chaser_order import LimitOrderChaser
from client.ex_client import ExSwapClient

import requests
from model import Symbol
from model import OrderSide
import log

logger = log.getLogger('BinanceSwapClient')

def get_tick_size(symbol: Symbol | str):
    """
    获取指定交易对的最小价格间隔(tickSize)
    
    Args:
        symbol (str): 交易对名称，例如 'BTCUSDT'
    
    Returns:
        float: 最小价格间隔，如果未找到则返回 None
    """
    # url = "https://api.binance.com/api/v3/exchangeInfo"
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    if isinstance(symbol, Symbol):
        symbol = symbol.binance()
    else:
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
            client=self,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            position_side=position_side,
        )
            

    def balance(self, coin):
        balance = self.exchange.fetch_balance()
        return balance[coin.upper()]['free']

    def cancel(self, custom_id: str, symbol: Symbol):
        return self.exchange.cancel_order(id='', symbol=symbol.ccxt(), params={
            'origClientOrderId': custom_id
        })

    #     'status': 'closed'
    def query_order(self, custom_id: str, symbol: Symbol):
        order = self.exchange.fetch_order(id='', symbol=symbol.ccxt(), params={
            'origClientOrderId': custom_id
        })
        return order

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

    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide, quantity: float, price: float | None = None, **kwargs):
        position_side = kwargs['position_side']

        if kwargs.get("chaser"):
            order_chaser = self.create_chaser(symbol, order_side, quantity, position_side)
            order_chaser.run()
            if order_chaser.order and order_chaser.order['status'] == 'closed':
                return order_chaser.order
            
        params = {
            'newClientOrderId': custom_id, 
            'positionSide': position_side
        }

        if kwargs.get('time_in_force') or kwargs.get('timeInForce'):
            params['timeInForce'] = kwargs['time_in_force'] or kwargs['timeInForce']

        order = self.exchange.create_order(
            symbol=symbol.binance(), 
            type='limit' if price else 'market', 
            side=order_side.value, 
            amount=quantity, 
            price=price, 
            params=params
        )

        return order        

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