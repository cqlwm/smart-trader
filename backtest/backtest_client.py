import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
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

    def __init__(self, initial_balance: float = 10000.0, maker_fee: float = 0.0002, taker_fee: float = 0.0004):
        self.exchange_name = 'backtest'
        self.exchange = None  # type: ignore  # 回测不需要真实交易所

        # 账户状态
        self._balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        # 订单和持仓管理
        self.orders: Dict[str, BacktestOrder] = {}
        self.positions: Dict[str, BacktestPosition] = {}
        self.order_history: List[BacktestOrder] = []

        # 锁保护并发访问
        self.lock = threading.RLock()

        # 当前市场价格（用于模拟成交）
        self.current_prices: Dict[str, float] = {}

        # 历史数据存储和时间同步
        self.historical_data: Dict[str, List[Kline]] = {}  # timeframe -> sorted klines
        self.current_timestamp: int = 0  # 当前回测时间戳

        logger.info(f"BacktestClient initialized with balance: {initial_balance}")

    def update_current_price(self, symbol: Symbol, price: float):
        """更新当前市场价格"""
        with self.lock:
            self.current_prices[symbol.binance()] = price

    def get_current_price(self, symbol: Symbol) -> float:
        """获取当前市场价格"""
        return self.current_prices.get(symbol.binance(), 0.0)

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        """返回模拟的交易对信息"""
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
        """获取账户余额"""
        if coin.upper() in ['USDT', 'USD', 'BUSD']:
            return self._balance
        return 0.0

    def cancel(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        """取消订单"""
        with self.lock:
            if custom_id in self.orders:
                order = self.orders[custom_id]
                if order.status == OrderStatus.OPEN:
                    order.status = OrderStatus.CANCELED
                    logger.debug(f"Order {custom_id} canceled")
                return order.to_dict()
            raise ValueError(f"Order {custom_id} not found")

    def query_order(self, custom_id: str, symbol: Symbol) -> Dict[str, Any]:
        """查询订单"""
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

        place_order_behavior = kwargs.get('place_order_behavior')

        order_type = 'limit' if price else 'market'
        current_price = self.get_current_price(symbol)

        if not current_price:
            logger.warning(f"No current price for {symbol.binance()}, skipping order")
            return None

        # 创建订单
        order = BacktestOrder(
            custom_id=custom_id,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            position_side=position_side,
            status=OrderStatus.OPEN,
            timestamp=int(time.time() * 1000)
        )

        with self.lock:
            self.orders[custom_id] = order

        # 模拟成交
        self._simulate_fill(order, current_price)

        return order.to_dict()

    def _simulate_fill(self, order: BacktestOrder, current_price: float):
        """模拟订单成交"""
        # 对于回测，立即以指定价格成交所有订单（模拟理想的成交条件）
        if order.order_type == 'market':
            # 市价单立即成交
            order.filled_quantity = order.quantity
            order.filled_price = current_price
            fee_rate = self.taker_fee
        elif order.order_type == 'limit' and order.price is not None:
            # 限价单：立即以指定价格成交（回测优化）
            order.filled_quantity = order.quantity
            order.filled_price = order.price
            fee_rate = self.maker_fee
        else:
            fee_rate = self.taker_fee  # 默认值

        order.status = OrderStatus.CLOSED

        # 计算手续费
        order.fee = order.filled_price * order.filled_quantity * fee_rate

        # 更新余额和持仓
        self._update_balance_and_position(order)

        # 记录到历史
        self.order_history.append(order)

        logger.debug(f"Order {order.custom_id} filled: {order.filled_quantity} @ {order.filled_price}")

    def _update_balance_and_position(self, order: BacktestOrder):
        """更新余额和持仓"""
        if order.side == OrderSide.BUY:
            # 买入：减少余额，增加持仓
            cost = order.filled_price * order.filled_quantity + order.fee
            self._balance -= cost

            pos_key = f"{order.symbol.binance()}_{order.position_side.value}"
            if pos_key in self.positions:
                pos = self.positions[pos_key]
                total_quantity = pos.quantity + order.filled_quantity
                total_cost = pos.entry_price * pos.quantity + order.filled_price * order.filled_quantity
                pos.entry_price = total_cost / total_quantity
                pos.quantity = total_quantity
            else:
                self.positions[pos_key] = BacktestPosition(
                    symbol=order.symbol,
                    side=order.position_side,
                    quantity=order.filled_quantity,
                    entry_price=order.filled_price
                )

        elif order.side == OrderSide.SELL:
            # 卖出：增加余额，减少持仓
            revenue = order.filled_price * order.filled_quantity - order.fee
            self._balance += revenue

            pos_key = f"{order.symbol.binance()}_{order.position_side.value}"
            if pos_key in self.positions:
                pos = self.positions[pos_key]
                if pos.quantity >= order.filled_quantity:
                    pos.quantity -= order.filled_quantity
                    if pos.quantity == 0:
                        del self.positions[pos_key]
                else:
                    logger.warning(f"Insufficient position for {pos_key}")

    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        """平仓所有持仓"""
        pos_key = f"{symbol}_{position_side}"
        if pos_key in self.positions:
            pos = self.positions[pos_key]
            # 创建平仓订单
            side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY
            current_price = self.get_current_price(pos.symbol)

            order = BacktestOrder(
                custom_id=f"close_{int(time.time())}",
                symbol=pos.symbol,
                side=side,
                quantity=pos.quantity,
                price=current_price,
                order_type='market',
                position_side=PositionSide(position_side),
                status=OrderStatus.CLOSED,
                timestamp=int(time.time() * 1000),
                filled_quantity=pos.quantity,
                filled_price=current_price,
                fee=current_price * pos.quantity * self.taker_fee
            )

            self._update_balance_and_position(order)
            self.order_history.append(order)
            del self.positions[pos_key]

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取持仓"""
        result = []
        for pos_key, pos in self.positions.items():
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
        """获取交易历史"""
        return [order.to_dict() for order in self.order_history]

    def get_final_balance(self) -> float:
        """获取最终余额"""
        return self._balance

    def load_historical_data(self, timeframe: str, klines: List[Kline]):
        """加载指定时间框架的历史数据"""
        with self.lock:
            # 确保数据按时间戳排序
            self.historical_data[timeframe] = sorted(klines, key=lambda k: k.timestamp)
            logger.info(f"Loaded {len(klines)} klines for timeframe {timeframe}")

    def update_current_timestamp(self, timestamp: int):
        """更新当前回测时间戳"""
        with self.lock:
            self.current_timestamp = timestamp

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100) -> List[List[Any]]:
        """返回截至当前回测时间的K线数据，模拟真实交易所的fetch_ohlcv接口"""
        with self.lock:
            if timeframe not in self.historical_data:
                logger.warning(f"No historical data available for timeframe {timeframe}")
                return []

            klines = self.historical_data[timeframe]

            # 过滤出当前时间戳之前的K线数据
            current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]

            if not current_klines:
                logger.warning(f"No klines available before timestamp {self.current_timestamp} for timeframe {timeframe}")
                return []

            # 返回最近的limit根K线
            recent_klines = current_klines[-limit:] if len(current_klines) >= limit else current_klines

            # 转换为OHLCV格式 [timestamp, open, high, low, close, volume]
            ohlcv_data = []
            for kline in recent_klines:
                ohlcv_data.append([
                    kline.timestamp,
                    kline.open,
                    kline.high,
                    kline.low,
                    kline.close,
                    kline.volume
                ])

            logger.debug(f"Returning {len(ohlcv_data)} klines for {symbol.binance()} {timeframe} at timestamp {self.current_timestamp}")
            return ohlcv_data
