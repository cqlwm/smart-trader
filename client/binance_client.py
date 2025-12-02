import ccxt
from typing import Any, Dict, List, Optional

from client.binance_chaser_order import LimitOrderChaser
from client.ex_client import ExSwapClient

import requests
from model import PositionSide, Symbol, PlaceOrderBehavior, SymbolInfo
from model import OrderSide
import log

logger = log.getLogger('BinanceSwapClient')

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
        self.exchange_info: Dict[str, Any] = {}

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        if not self.exchange_info:
            response = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
            self.exchange_info: Dict[str, Any] = response.json()
        
        tick_size=0.0
        min_price=0.0
        max_price=0.0
        step_size=0.0
        min_qty=0.0
        max_qty=0.0

        for symbol_info in self.exchange_info['symbols']:
            if symbol_info['symbol'] == symbol.binance():
                for filter_info in symbol_info['filters']:
                    if filter_info['filterType'] == 'PRICE_FILTER':
                        tick_size=float(filter_info['tickSize'])
                        min_price=float(filter_info['minPrice'])
                        max_price=float(filter_info['maxPrice'])

                    if filter_info['filterType'] == 'LOT_SIZE':
                        step_size=float(filter_info['stepSize'])
                        min_qty=float(filter_info['minQty'])
                        max_qty=float(filter_info['maxQty'])
        
        if not tick_size or not min_price or not max_price or not step_size or not min_qty or not max_qty:
            raise ValueError(f"获取{symbol}的symbol info失败")

        return SymbolInfo(
            symbol=symbol,
            tick_size=tick_size,
            min_price=min_price,
            max_price=max_price,
            step_size=step_size,
            min_qty=min_qty,
            max_qty=max_qty,
        )

    def create_chaser(self, symbol: Symbol, order_side: OrderSide, quantity: float, position_side: str, place_order_behavior: PlaceOrderBehavior) -> LimitOrderChaser:
        return LimitOrderChaser(
            client=self,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
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
            behavior_value = PlaceOrderBehavior.NORMAL.value

        if 'chaser' in behavior_value:
            order_chaser = self.create_chaser(symbol, order_side, quantity, position_side, PlaceOrderBehavior(behavior_value))
            order_chaser.first_price = kwargs.pop('first_price', None)
            
            ok: bool = order_chaser.run()
            if ok:
                return order_chaser.order
            else:
                logger.error(f"追单失败, 执行常规订单, price: {price}")

        params: Dict[str, Any] = {'newClientOrderId': custom_id}
        if position_side:
            params['positionSide'] = position_side

        order_type = 'limit' if price else 'market'

        # 只在限价单时设置timeInForce
        if order_type == 'limit' and (kwargs.get('time_in_force') or kwargs.get('timeInForce')):
            params['timeInForce'] = kwargs['time_in_force'] or kwargs['timeInForce']

        try:
            symbol_info = self.symbol_info(symbol)

            price = symbol_info.format_price(price) if price else price
            quantity = symbol_info.format_qty(quantity)

            order: Dict[str, Any] = self.exchange.create_order(  # type: ignore
                symbol=symbol.ccxt(),
                type=order_type,
                side=order_side.value,
                amount=quantity,
                price=price,
                params=params
            )
            return order
        except Exception as e:
            logger.debug(f"下单失败: symbol: {symbol.binance()}, type: {order_type}, side: {order_side.value}, quantity: {quantity}, price: {price}, params: {params}, error: {str(e)}")
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