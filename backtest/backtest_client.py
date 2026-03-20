from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import threading

from client.ex_client import ExSwapClient
from model import Symbol, SymbolInfo, OrderSide, PositionSide, OrderStatus, Kline
import log

logger = log.getLogger(__name__)


@dataclass
class BacktestOrder:
    """回测订单"""
    custom_id: str
    symbol: Symbol
    side: OrderSide
    quantity: float
    price: Optional[float]
    order_type: str  # 'market' or 'limit'
    position_side: PositionSide
    status: OrderStatus
    timestamp: int
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    fee: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.custom_id,
            'clientOrderId': self.custom_id,
            'symbol': self.symbol.binance(),
            'side': self.side.value,
            'position_side': self.position_side.value,
            'type': self.order_type,
            'price': self.price,
            'amount': self.quantity,
            'filled': self.filled_quantity,
            'filled_quantity': self.filled_quantity,
            'remaining': self.quantity - self.filled_quantity,
            'filled_price': self.filled_price,
            'cost': self.filled_price * self.filled_quantity if self.filled_price else 0,
            'status': self.status.value,
            'timestamp': self.timestamp,
            'fee': self.fee
        }


@dataclass
class BacktestPosition:
    """回测持仓"""
    symbol: Symbol
    side: PositionSide
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0


class BacktestClient(ExSwapClient):
    """回测客户端，模拟交易操作"""

    def __init__(self, initial_balance: float = 10000.0, maker_fee: float = 0.0002,
                 taker_fee: float = 0.0004,
                 symbol_infos: Optional[Dict[str, SymbolInfo]] = None):
        self.exchange_name = 'backtest'
        self.exchange = None  # type: ignore

        self._balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        # symbol -> SymbolInfo override
        self._symbol_infos: Dict[str, SymbolInfo] = symbol_infos or {}

        # 订单和持仓管理
        self.orders: Dict[str, BacktestOrder] = {}
        self._positions: Dict[str, BacktestPosition] = {}
        self.order_history: List[BacktestOrder] = []

        self.lock = threading.RLock()

        self.current_prices: Dict[str, float] = {}

        self.historical_data: Dict[str, List[Kline]] = {}
        self.current_timestamp: int = 0

        logger.info(f"BacktestClient initialized with balance: {initial_balance}")

    def update_current_price(self, symbol: Symbol, price: float):
        """更新当前市场价格，同时检查挂单成交并更新持仓盈亏"""
        with self.lock:
            self.current_prices[symbol.binance()] = price

    def update_current_timestamp(self, timestamp: int):
        """更新当前回测时间戳"""
        with self.lock:
            self.current_timestamp = timestamp

    def check_pending_orders(self, kline: Kline):
        """每根K线处理完后检查限价挂单是否触及成交"""
        with self.lock:
            pending = [o for o in self.orders.values() if o.status == OrderStatus.OPEN
                       and o.order_type == 'limit' and o.symbol.binance() == kline.symbol.binance()]
            for order in pending:
                triggered = False
                if order.side == OrderSide.BUY and kline.low <= order.price:
                    triggered = True
                elif order.side == OrderSide.SELL and kline.high >= order.price:
                    triggered = True

                if triggered:
                    self._fill_order(order)

        # 更新持仓浮盈
        self._update_unrealized_pnl(kline.symbol, kline.close)

    def _update_unrealized_pnl(self, symbol: Symbol, price: float):
        with self.lock:
            for pos in self._positions.values():
                if pos.symbol.binance() != symbol.binance():
                    continue
                if pos.side == PositionSide.LONG:
                    pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity

    def get_current_price(self, symbol: Symbol) -> float:
        return self.current_prices.get(symbol.binance(), 0.0)

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        key = symbol.binance()
        if key in self._symbol_infos:
            return self._symbol_infos[key]
        return SymbolInfo(
            symbol=symbol,
            tick_size=0.01,
            min_price=0.01,
            max_price=1000000.0,
            step_size=0.001,
            min_qty=0.001,
            max_qty=100000.0
        )

    def balance(self, coin: str) -> float:
        if coin.upper() in ['USDT', 'USD', 'BUSD', 'USDC']:
            return self._balance
        return 0.0

    def cancel(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        with self.lock:
            if custom_id in self.orders:
                order = self.orders[custom_id]
                if order.status == OrderStatus.OPEN:
                    order.status = OrderStatus.CANCELED
                    logger.debug(f"Order {custom_id} canceled")
                return order.to_dict()
            raise ValueError(f"Order {custom_id} not found")

    def query_order(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        with self.lock:
            if custom_id in self.orders:
                return self.orders[custom_id].to_dict()
            raise ValueError(f"Order {custom_id} not found")

    def place_order_v2(self, custom_id: str, symbol: Symbol, order_side: OrderSide,
                      quantity: float, price: Optional[float] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """下单"""
        position_side = kwargs.get('position_side', PositionSide.LONG)
        if isinstance(position_side, str):
            position_side = PositionSide(position_side)

        order_type = 'limit' if price else 'market'
        current_price = self.get_current_price(symbol)

        logger.debug(f"Placing order {custom_id}: symbol={symbol.binance()}, current_price={current_price}")

        if not current_price:
            logger.warning(f"No current price for {symbol.binance()}, skipping order")
            return None

        order = BacktestOrder(
            custom_id=custom_id,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            position_side=position_side,
            status=OrderStatus.OPEN,
            timestamp=self.current_timestamp  # 使用回测时间，非 wall clock
        )

        with self.lock:
            self.orders[custom_id] = order

        if order_type == 'market':
            self._fill_order(order, fill_price=current_price)
        # 限价单不立即成交，等待 check_pending_orders 触发

        return order.to_dict()

    def _fill_order(self, order: BacktestOrder, fill_price: Optional[float] = None):
        """成交订单"""
        if order.order_type == 'market':
            order.filled_price = fill_price or self.get_current_price(order.symbol)
            fee_rate = self.taker_fee
        else:
            order.filled_price = order.price  # type: ignore[assignment]
            fee_rate = self.maker_fee

        order.filled_quantity = order.quantity
        order.status = OrderStatus.CLOSED
        order.fee = order.filled_price * order.filled_quantity * fee_rate

        self._update_balance_and_position(order)
        self.order_history.append(order)

        logger.info(f"Order {order.custom_id} filled: {order.filled_quantity} @ {order.filled_price}, "
                    f"total orders: {len(self.order_history)}")

    def _update_balance_and_position(self, order: BacktestOrder):
        """更新余额和持仓。

        开仓：LONG+BUY 或 SHORT+SELL → 增加持仓
        平仓：LONG+SELL 或 SHORT+BUY → 减少/关闭持仓
        """
        pos_key = f"{order.symbol.binance()}_{order.position_side.value}"
        is_open = (
            (order.position_side == PositionSide.LONG and order.side == OrderSide.BUY) or
            (order.position_side == PositionSide.SHORT and order.side == OrderSide.SELL)
        )

        if is_open:
            # 开仓：占用资金
            cost = order.filled_price * order.filled_quantity + order.fee
            self._balance -= cost

            if pos_key in self._positions:
                pos = self._positions[pos_key]
                total_quantity = pos.quantity + order.filled_quantity
                total_cost = pos.entry_price * pos.quantity + order.filled_price * order.filled_quantity
                pos.entry_price = total_cost / total_quantity
                pos.quantity = total_quantity
            else:
                self._positions[pos_key] = BacktestPosition(
                    symbol=order.symbol,
                    side=order.position_side,
                    quantity=order.filled_quantity,
                    entry_price=order.filled_price
                )
        else:
            # 平仓：释放资金
            revenue = order.filled_price * order.filled_quantity - order.fee
            self._balance += revenue

            if pos_key in self._positions:
                pos = self._positions[pos_key]
                if pos.quantity >= order.filled_quantity:
                    pos.quantity -= order.filled_quantity
                    if pos.quantity == 0:
                        del self._positions[pos_key]
                else:
                    logger.warning(f"Insufficient position for {pos_key}")

    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        """平仓所有持仓"""
        pos_key = f"{symbol}_{position_side}"
        if pos_key in self._positions:
            pos = self._positions[pos_key]
            side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY
            current_price = self.get_current_price(pos.symbol)

            order = BacktestOrder(
                custom_id=f"close_{self.current_timestamp}",
                symbol=pos.symbol,
                side=side,
                quantity=pos.quantity,
                price=current_price,
                order_type='market',
                position_side=PositionSide(position_side),
                status=OrderStatus.CLOSED,
                timestamp=self.current_timestamp,  # 使用回测时间
                filled_quantity=pos.quantity,
                filled_price=current_price,
                fee=current_price * pos.quantity * self.taker_fee
            )

            self._update_balance_and_position(order)
            self.order_history.append(order)
            if pos_key in self._positions:
                del self._positions[pos_key]

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []
        for pos_key, pos in self._positions.items():
            if symbol is None or symbol in pos_key:
                result.append({
                    'symbol': pos.symbol.binance(),
                    'side': pos.side.value,
                    'contracts': pos.quantity,
                    'entryPrice': pos.entry_price,
                    'unrealizedProfit': pos.unrealized_pnl
                })
        return result

    def get_trade_history(self) -> List[Dict[str, Any]]:
        return [order.to_dict() for order in self.order_history]

    def get_final_balance(self) -> float:
        return self._balance

    def load_historical_data(self, symbol: Symbol, timeframe: str, klines: List[Kline]):
        with self.lock:
            key = f"{symbol.binance()}_{timeframe}"
            self.historical_data[key] = sorted(klines, key=lambda k: k.timestamp)
            logger.info(f"Loaded {len(klines)} klines for {symbol.binance()} timeframe {timeframe}")

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100) -> List[Kline]:
        """返回截至当前回测时间的K线数据"""
        with self.lock:
            key = f"{symbol.binance()}_{timeframe}"
            if key not in self.historical_data:
                logger.warning(f"No historical data available for {symbol.binance()} timeframe {timeframe}")
                return []

            klines = self.historical_data[key]
            current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]

            if not current_klines:
                logger.warning(f"No klines available before timestamp {self.current_timestamp} for timeframe {timeframe}")
                return []

            recent_klines = current_klines[-limit:] if len(current_klines) >= limit else current_klines

            return recent_klines
