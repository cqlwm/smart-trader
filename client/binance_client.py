import time

import ccxt
from typing import Any, Dict, List, Optional, Literal

from persistence.kline_data_store import KlineDataStore
from client.binance_chaser_order import LimitOrderChaser
from client.ex_client import ExSwapClient

import requests
from model import Order, PositionSide, Symbol, PlaceOrderBehavior, SymbolInfo
from model import OrderSide, OrderStatus, Kline
from persistence.order_repository import InMemoryOrderRepository, OrderRepository
import log
from ccxt.base.types import ConstructorArgs

logger = log.getLogger('BinanceSwapClient')

class BinanceSwapClient(ExSwapClient):
    def __init__(self, api_key: str, api_secret: str, is_test: bool = False, order_repo: OrderRepository | None = None, data_store: KlineDataStore | None = None):
        self.exchange_name = 'binance'
        self.order_repo = order_repo or InMemoryOrderRepository()
        self.data_store = data_store

        self.exchange = ccxt.binance(ConstructorArgs(
            apiKey=api_key,
            secret=api_secret,
            options={
                "defaultType": "future",
            }
        ))
        self.exchange.enable_demo_trading(is_test)
        self.exchange.load_markets()
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

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        if start_time is not None or end_time is not None:
            if self.data_store:
                from datetime import datetime, timezone as tz
                start_dt = datetime.fromtimestamp(start_time / 1000, tz=tz.utc) if start_time else None
                end_dt = datetime.fromtimestamp(end_time / 1000, tz=tz.utc) if end_time else None

                start_str = start_dt.isoformat() if start_dt else ""
                end_str = end_dt.isoformat() if end_dt else ""

                file_path = self.data_store.ensure_data(
                    symbol, timeframe,
                    start_str or "2020-01-01",
                    end_str or datetime.now(tz=tz.utc).isoformat(),
                    "data",
                )
                klines = self.data_store.load_csv(file_path, symbol, timeframe)
                return self._filter_klines_by_time(klines, start_time, end_time, limit)
            return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit, start_time, end_time)
        return self._fetch_ohlcv_via_ccxt(symbol, timeframe, limit)

    def _fetch_ohlcv_via_ccxt(self, symbol: Symbol, timeframe: str, limit: int = 100,
                               start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        since = start_time
        list_ohlcv = self.exchange.fetch_ohlcv(
            symbol.ccxt(), timeframe, since=since, limit=limit
        )
        klines: list[Kline] = []
        for ohlcv in list_ohlcv:
            ts = ohlcv[0]
            if end_time is not None and ts > end_time:
                break
            klines.append(
                Kline(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=ohlcv[1],
                    high=ohlcv[2],
                    low=ohlcv[3],
                    close=ohlcv[4],
                    volume=ohlcv[5],
                    finished=True
                )
            )

        if klines:
            timeframe_ms = self.exchange.parse_timeframe(timeframe)
            klines[-1].finished = klines[-1].timestamp + timeframe_ms * 1000 <= int(time.time() * 1000)

        return klines[-limit:] if len(klines) > limit else klines

    @staticmethod
    def _filter_klines_by_time(klines: list[Kline], start_time: int | None,
                                end_time: int | None, limit: int) -> list[Kline]:
        filtered = [
            k for k in klines
            if (start_time is None or k.timestamp >= start_time)
            and (end_time is None or k.timestamp <= end_time)
        ]
        return filtered[-limit:] if len(filtered) > limit else filtered

    def create_chaser(self, symbol: Symbol, order_side: OrderSide, quantity: float, position_side: str, place_order_behavior: PlaceOrderBehavior, strategy_id: str = "") -> LimitOrderChaser:
        return LimitOrderChaser(
            client=self,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            strategy_id=strategy_id,
            position_side=position_side,
            place_order_behavior=place_order_behavior,
        )

    def balance(self, coin: str) -> float:
        balance = self.exchange.fetch_balance()  # type: ignore
        return balance[coin.upper()]['free']

    def cancel(self, custom_id: str, symbol: Symbol) -> Order | None:
        existing = self.order_repo.find_by_id(custom_id)
        try:
            self.exchange.cancel_order(id='', symbol=symbol.ccxt(), params={  # type: ignore
                'origClientOrderId': custom_id
            })
        except Exception as e:
            logger.error("Cancel order failed: %s", e)
            return existing

        if existing:
            now = int(time.time() * 1000)
            updated = existing.with_status(OrderStatus.CANCELED, updated_at=now)
            self.order_repo.save(updated)
            return updated
        return None

    def query_order(self, custom_id: str, symbol: Symbol) -> Order | None:
        existing = self.order_repo.find_by_id(custom_id)
        try:
            raw = self.exchange.fetch_order(id='', symbol=symbol.ccxt(), params={  # type: ignore
                'origClientOrderId': custom_id
            })
            if existing and raw:
                status = OrderStatus._normalize_status(raw.get('status', ''))
                now = int(time.time() * 1000)
                filled_qty = float(raw.get('filled', 0) or 0)
                filled_price = float(raw.get('average', 0) or raw.get('price', 0) or 0)
                fee = float(raw.get('fee', {}).get('cost', 0) or 0) if isinstance(raw.get('fee'), dict) else 0
                updated = existing.with_status(
                    OrderStatus(status),
                    updated_at=now,
                    filled_quantity=filled_qty,
                    filled_price=filled_price,
                    fee=fee,
                )
                self.order_repo.save(updated)
                return updated
        except Exception as e:
            logger.debug("Query order failed: %s", e)
        return existing

    def place_order_v2(
        self,
        strategy_id: str,
        custom_id: str,
        symbol: Symbol,
        order_side: OrderSide,
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Optional[Order]:
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
            order_chaser = self.create_chaser(symbol, order_side, quantity, position_side, PlaceOrderBehavior(behavior_value), strategy_id=strategy_id)
            order_chaser.first_price = kwargs.pop('first_price', None)

            ok: bool = order_chaser.run()
            if ok and order_chaser.order:
                return order_chaser.order
            else:
                logger.error(f"追单失败, 执行常规订单, price: {price}")

        params: Dict[str, Any] = {'newClientOrderId': custom_id}
        if position_side:
            params['positionSide'] = position_side

        order_type: Literal['limit', 'market'] = 'limit' if price else 'market'

        # 只在限价单时设置timeInForce
        if order_type == 'limit' and (kwargs.get('time_in_force') or kwargs.get('timeInForce')):
            params['timeInForce'] = kwargs['time_in_force'] or kwargs['timeInForce']

        try:
            symbol_info = self.symbol_info(symbol)

            price = symbol_info.format_price(price) if price else price
            quantity = symbol_info.format_qty(quantity)

            raw_order: Dict[str, Any] = self.exchange.create_order(  # type: ignore
                symbol=symbol.ccxt(),
                type=order_type,
                side=order_side.val(),
                amount=quantity,
                price=price,
                params=params
            )
            now = int(time.time() * 1000)
            order = Order(
                order_id=raw_order.get('clientOrderId', custom_id),
                strategy_id=strategy_id,
                symbol=symbol,
                side=order_side,
                position_side=PositionSide(position_side),
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.OPEN,
                created_at=now,
                updated_at=now,
                filled_quantity=float(raw_order.get('filled', 0) or 0),
                filled_price=float(raw_order.get('average', 0) or raw_order.get('price', 0) or 0),
                fee=float(raw_order.get('fee', {}).get('cost', 0) or 0) if isinstance(raw_order.get('fee'), dict) else 0,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            self.order_repo.save(order)
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
                    pass
        if auto_cancel:
            open_orders: List[Dict[str, Any]] = self.exchange.fetch_open_orders(symbol)  # type: ignore
            for order in open_orders:
                self.exchange.cancel_order(order['id'], symbol)  # type: ignore

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if symbol is not None:
            return self.exchange.fetch_positions([symbol])  # type: ignore
        else:
            return self.exchange.fetch_positions()  # type: ignore

    def get_trade_history(self) -> list[dict[str, Any]]:
        return [self._order_to_dict(o) for o in self.order_repo.find_history()]

    @staticmethod
    def _order_to_dict(order: Order) -> dict[str, Any]:
        return {
            'id': order.order_id,
            'clientOrderId': order.order_id,
            'symbol': order.symbol.binance(),
            'side': order.side.value,
            'position_side': order.position_side.value,
            'type': order.order_type,
            'price': order.price,
            'amount': order.quantity,
            'filled': order.filled_quantity,
            'filled_quantity': order.filled_quantity,
            'remaining': order.quantity - order.filled_quantity,
            'filled_price': order.filled_price,
            'cost': order.filled_price * order.filled_quantity if order.filled_price else 0,
            'status': order.status.value,
            'timestamp': order.created_at,
            'fee': order.fee,
        }
