import ccxt
from typing import Any, Dict, List, Optional

from client.binance_chaser_order import LimitOrderChaser
from client.ex_client import ExSwapClient

import requests
from model import PositionSide, Symbol, PlaceOrderBehavior
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
        response = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
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
    def __init__(self, api_key: str, api_secret: str, is_test: bool = False):
        self.exchange_name = 'binance'
        self.exchange = ccxt.binance({  # type: ignore
            'apiKey': api_key,
            'secret': api_secret,
            'options': {
                'defaultType': 'future',
            }
        })
        self.exchange.set_sandbox_mode(is_test)

    def create_chaser(self, symbol: Symbol, order_side: OrderSide, quantity: float, position_side: str, place_order_behavior: PlaceOrderBehavior) -> LimitOrderChaser:
        return LimitOrderChaser(
            client=self,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            tick_size=get_tick_size(symbol) or 0.01,
            position_side=position_side,
            place_order_behavior=place_order_behavior,
        )
    
    def balance(self, coin: str) -> float:
        balance = self.exchange.fetch_balance()  # type: ignore
        return balance[coin.upper()]['free']

    def cancel(self, custom_id: str, symbol: Symbol):
        return self.exchange.cancel_order(id='', symbol=symbol.ccxt(), params={  # type: ignore
            'origClientOrderId': custom_id
        })

    def query_order(self, custom_id: str, symbol: Symbol):
        order = self.exchange.fetch_order(id='', symbol=symbol.ccxt(), params={  # type: ignore
            'origClientOrderId': custom_id
        })
        return order

    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide, quantity: float, price: Optional[float] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        position_side = kwargs.pop('position_side', None)
        if isinstance(position_side, PositionSide):
            position_side = position_side.value
        elif position_side and position_side.lower() in ['long', 'short']:
            position_side = position_side.lower()
        else:
            raise ValueError(f"position_side 必须是 PositionSide 枚举值或 'long'/'short' 字符串, 但 got {position_side}")
            
        place_order_behavior: Optional[PlaceOrderBehavior] = kwargs.get("place_order_behavior")

        if isinstance(place_order_behavior, PlaceOrderBehavior):
            behavior_value: str = place_order_behavior.value
        else:
            behavior_value = place_order_behavior or ''

        if 'chaser' in behavior_value:
            order_chaser = self.create_chaser(symbol, order_side, quantity, position_side, PlaceOrderBehavior(behavior_value))
            ok: bool = order_chaser.run()  # type: ignore
            if ok:
                return order_chaser.order
            else:
                logger.error("追单失败, 市价执行")

        params: Dict[str, Any] = {'newClientOrderId': custom_id}
        if position_side:
            params['positionSide'] = position_side

        order_type = 'limit' if price else 'market'

        # 只在限价单时设置timeInForce
        if order_type == 'limit' and (kwargs.get('time_in_force') or kwargs.get('timeInForce')):
            params['timeInForce'] = kwargs['time_in_force'] or kwargs['timeInForce']

        try:
            order: Dict[str, Any] = self.exchange.create_order(  # type: ignore
                symbol=symbol.binance(),
                type=order_type,
                side=order_side.value,
                amount=quantity,
                price=price,
                params=params
            )
            return order
        except Exception as e:
            logger.error(f"下单失败: symbol: {symbol.binance()}, type: {order_type}, side: {order_side.value}, quantity: {quantity}, price: {price}, params: {params}", stack_info=True)
            raise e

    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        positions: List[Dict[str, Any]] = self.positions(symbol)
        for position in positions:
            if position['side'] == position_side:
                quantity: float = position['contracts']
                if quantity > 0:
                    # Note: place_order method not found in parent class, this may need to be addressed
                    pass  # self.place_order(None, symbol, order_side, position_side, quantity)
        if auto_cancel:
            open_orders: List[Dict[str, Any]] = self.exchange.fetch_open_orders(symbol)  # type: ignore
            for order in open_orders:
                self.exchange.cancel_order(order['id'], symbol)  # type: ignore

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if symbol is not None:
            return self.exchange.fetch_positions([symbol])  # type: ignore
        else:
            return self.exchange.fetch_positions()  # type: ignore