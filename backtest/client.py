import logging
from dataclasses import dataclass
from typing import Any

from client.ex_client import ExSwapClient
from model import Symbol, SymbolInfo, Order, OrderSide, PositionSide, OrderStatus, Kline
from persistence.order_repository import OrderRepository
from persistence.kline_data_store import KlineDataStore

logger = logging.getLogger(__name__)


@dataclass
class _Position:
    symbol: Symbol
    side: PositionSide
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0


class BacktestClient(ExSwapClient):
    """回测客户端，模拟交易操作（同步执行，无需锁）"""

    def __init__(
        self,
        order_repo: OrderRepository,
        data_store: KlineDataStore | None = None,
        initial_balance: float = 10000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        symbol_infos: dict[str, SymbolInfo] | None = None,
    ) -> None:
        self.exchange_name = 'backtest'
        self.exchange = None  # type: ignore

        self.data_store = data_store
        self.order_repo = order_repo

        self.initial_balance = initial_balance
        self._balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        self._symbol_infos: dict[str, SymbolInfo] = symbol_infos or {}

        self._positions: dict[str, _Position] = {}

        self.current_prices: dict[str, float] = {}
        self.current_timestamp: int = 0

        self._kline_cache: dict[str, list[Kline]] = {}

        logger.info("BacktestClient initialized with balance: %s", initial_balance)

    def _store_klines(self, symbol: Symbol, timeframe: str, klines: list[Kline]) -> None:
        key = f"{symbol.binance()}_{timeframe}"
        self._kline_cache[key] = sorted(klines, key=lambda k: k.timestamp)

    def _ensure_klines(self, symbol: Symbol, timeframe: str, limit: int, start_time: int | None = None, end_time: int | None = None) -> None:
        """Lazy-load klines from data_store if not cached."""
        key = f"{symbol.binance()}_{timeframe}"
        if key in self._kline_cache:
            return

        if self.data_store is None:
            logger.warning("No data_store configured, cannot load %s %s", symbol.binance(), timeframe)
            return

        tf_ms = self._parse_timeframe_to_ms(timeframe)
        if tf_ms == 0:
            logger.warning("Unknown timeframe: %s", timeframe)
            return

        # If start_time and end_time are provided, use those (for full backtest range)
        if start_time is not None and end_time is not None:
            start_ts = start_time
            end_ts = end_time
        else:
            buffer_ratio = 1.3
            range_ms = int(limit * tf_ms * buffer_ratio)
            end_ts = self.current_timestamp
            start_ts = end_ts - range_ms

        from datetime import datetime, timezone
        start_date = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
        end_date = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')

        try:
            file_path = self.data_store.ensure_data(symbol, timeframe, start_date, end_date)
            klines = self.data_store.load_csv(file_path, symbol, timeframe)
            if klines:
                self._store_klines(symbol, timeframe, klines)
                logger.info("Lazy-loaded %d klines for %s %s", len(klines), symbol.binance(), timeframe)
        except FileNotFoundError:
            logger.warning("Data file not found for %s %s", symbol.binance(), timeframe)
        except ValueError as e:
            logger.warning("Failed to load data for %s %s: %s", symbol.binance(), timeframe, e)

    @staticmethod
    def _parse_timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string (e.g. '5m', '1h', '1d') to milliseconds."""
        try:
            import ccxt
            return ccxt.Exchange.parse_timeframe(timeframe) * 1000
        except Exception:
            return 0

    def update_current_price(self, symbol: Symbol, price: float) -> None:
        self.current_prices[symbol.binance()] = price

    def update_current_timestamp(self, timestamp: int) -> None:
        self.current_timestamp = timestamp

    def check_pending_orders(self, kline: Kline) -> None:
        open_orders = self.order_repo.find_open_orders()
        pending = [o for o in open_orders
                   if o.order_type == 'limit'
                   and o.symbol.binance() == kline.symbol.binance()]
        for order in pending:
            triggered = False
            if order.side == OrderSide.BUY and kline.low <= (order.price or 0):
                triggered = True
            elif order.side == OrderSide.SELL and kline.high >= (order.price or 0):
                triggered = True

            if triggered:
                self._fill_order(order)

        self._update_unrealized_pnl(kline.symbol, kline.close)

    def _update_unrealized_pnl(self, symbol: Symbol, price: float) -> None:
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

    def cancel(self, custom_id: str, symbol: Symbol) -> Order | None:
        order = self.order_repo.find_by_id(custom_id)
        if order and OrderStatus.is_open(order.status):
            updated = order.with_status(OrderStatus.CANCELED, updated_at=self.current_timestamp)
            self.order_repo.save(updated)
            logger.debug("Order %s canceled", custom_id)
            return updated
        return order

    def query_order(self, custom_id: str, symbol: Symbol) -> Order | None:
        return self.order_repo.find_by_id(custom_id)

    def place_order_v2(
        self,
        strategy_id: str,
        custom_id: str,
        symbol: Symbol,
        order_side: OrderSide,
        quantity: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        **kwargs: Any,
    ) -> Order | None:
        position_side = kwargs.get('position_side', PositionSide.LONG)
        if isinstance(position_side, str):
            position_side = PositionSide(position_side)

        order_type = 'limit' if price else 'market'
        current_price = self.get_current_price(symbol)

        logger.debug("Placing order %s: symbol=%s, current_price=%s",
                     custom_id, symbol.binance(), current_price)

        if not current_price:
            logger.warning("No current price for %s, skipping order", symbol.binance())
            return None

        order = Order(
            order_id=custom_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=order_side,
            position_side=position_side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=OrderStatus.OPEN,
            created_at=self.current_timestamp,
            updated_at=self.current_timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        self.order_repo.save(order)

        if order_type == 'market':
            self._fill_order(order, fill_price=current_price)

        return order

    def _fill_order(self, order: Order, fill_price: float | None = None) -> None:
        if order.order_type == 'market':
            actual_fill_price = fill_price or self.get_current_price(order.symbol)
            fee_rate = self.taker_fee
        else:
            actual_fill_price = order.price or self.get_current_price(order.symbol)
            fee_rate = self.maker_fee

        fee = actual_fill_price * order.quantity * fee_rate
        filled = order.with_status(
            OrderStatus.CLOSED,
            updated_at=self.current_timestamp,
            filled_quantity=order.quantity,
            filled_price=actual_fill_price,
            fee=fee,
        )
        self.order_repo.save(filled)

        self._update_balance_and_position(filled)

        logger.info("Order %s filled: %s @ %s",
                    filled.order_id, filled.filled_quantity, filled.filled_price)

    def _update_balance_and_position(self, order: Order) -> None:
        pos_key = f"{order.symbol.binance()}_{order.position_side.value}"
        is_open = (
            (order.position_side == PositionSide.LONG and order.side == OrderSide.BUY) or
            (order.position_side == PositionSide.SHORT and order.side == OrderSide.SELL)
        )

        if is_open:
            cost = order.filled_price * order.filled_quantity + order.fee
            self._balance -= cost

            if pos_key in self._positions:
                pos = self._positions[pos_key]
                total_quantity = pos.quantity + order.filled_quantity
                total_cost = pos.entry_price * pos.quantity + order.filled_price * order.filled_quantity
                pos.entry_price = total_cost / total_quantity
                pos.quantity = total_quantity
            else:
                self._positions[pos_key] = _Position(
                    symbol=order.symbol,
                    side=order.position_side,
                    quantity=order.filled_quantity,
                    entry_price=order.filled_price,
                )
        else:
            revenue = order.filled_price * order.filled_quantity - order.fee
            self._balance += revenue

            if pos_key in self._positions:
                pos = self._positions[pos_key]
                if pos.quantity >= order.filled_quantity:
                    pos.quantity -= order.filled_quantity
                    if pos.quantity == 0:
                        del self._positions[pos_key]
                else:
                    logger.warning("Insufficient position for %s", pos_key)

    def close_position(self, symbol: str, position_side: str, auto_cancel: bool = True) -> None:
        pos_key = f"{symbol}_{position_side}"
        if pos_key in self._positions:
            pos = self._positions[pos_key]
            side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY
            current_price = self.get_current_price(pos.symbol)
            fee = current_price * pos.quantity * self.taker_fee

            order = Order(
                order_id=f"close_{self.current_timestamp}",
                strategy_id="",
                symbol=pos.symbol,
                side=side,
                position_side=PositionSide(position_side),
                order_type='market',
                quantity=pos.quantity,
                price=current_price,
                status=OrderStatus.CLOSED,
                created_at=self.current_timestamp,
                updated_at=self.current_timestamp,
                filled_quantity=pos.quantity,
                filled_price=current_price,
                fee=fee,
            )
            self.order_repo.save(order)
            self._update_balance_and_position(order)
            if pos_key in self._positions:
                del self._positions[pos_key]

    def positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
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

    def get_trade_history(self) -> list[dict[str, Any]]:
        all_orders = []
        for order in self.order_repo.find_history():
            all_orders.append(self._order_to_dict(order))
        return all_orders

    def get_final_balance(self) -> float:
        return self._balance

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 100,
                    start_time: int | None = None, end_time: int | None = None) -> list[Kline]:
        self._ensure_klines(symbol, timeframe, limit, start_time, end_time)

        key = f"{symbol.binance()}_{timeframe}"
        if key not in self._kline_cache:
            return []

        klines = self._kline_cache[key]

        if start_time is not None or end_time is not None:
            klines = [k for k in klines
                      if (start_time is None or k.timestamp >= start_time)
                      and (end_time is None or k.timestamp < end_time)]
            if limit and len(klines) > limit:
                return klines[-limit:]
            return klines

        current_klines = [k for k in klines if k.timestamp <= self.current_timestamp]
        if not current_klines:
            logger.warning("No klines available before timestamp %d for timeframe %s",
                          self.current_timestamp, timeframe)
            return []

        if limit and len(current_klines) >= limit:
            return current_klines[-limit:]
        return current_klines

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
