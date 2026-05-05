import secrets
import threading
from typing import Any, List, Callable, Dict
from client.ex_client import ExSwapClient, ExClient
from strategy import SimpleStrategy
from model import OrderSide, OrderStatus, PlaceOrderBehavior, PositionSide
import logging
from pydantic import BaseModel, ConfigDict, field_serializer
from model import Symbol
from strategy import Signal

from persistence.repository import StrategyRepository
from persistence.sqlite_repo import SQLiteStrategyRepository
from strategy.registry import register_strategy

logger = logging.getLogger(__name__)

def build_order_id(side: OrderSide):
    return f'{side.value}{secrets.token_hex(nbytes=5)}'


class OrderExtension(BaseModel):
    """Mutable order extension data that lives alongside the immutable Order."""
    entry_id: str
    side: OrderSide
    price: float
    quantity: float
    fixed_take_profit_rate: float
    signal_min_take_profit_rate: float
    exit_price: float | None = None
    status: str | None = None
    exit_id: str | None = None

    stop_loss_rate: float = 0.0
    enable_stop_loss: bool = False
    trailing_stop_rate: float = 0.0
    enable_trailing_stop: bool = False
    trailing_stop_activation_profit_rate: float = 0.0
    current_stop_price: float | None = None

    def to_db_dict(self, symbol: str) -> dict:
        return {
            'id': self.entry_id,
            'symbol': symbol,
            'position_side': "LONG" if self.side == OrderSide.BUY else "SHORT",
            'order_side': self.side.value,
            'entry_price': self.price,
            'exit_price': self.exit_price,
            'quantity': self.quantity,
            'entry_order_id': self.entry_id,
            'exit_order_id': self.exit_id,
            'status': self.status or "pending",
            'extra_data': {
                'fixed_take_profit_rate': self.fixed_take_profit_rate,
                'signal_min_take_profit_rate': self.signal_min_take_profit_rate,
                'stop_loss_rate': self.stop_loss_rate,
                'enable_stop_loss': self.enable_stop_loss,
                'trailing_stop_rate': self.trailing_stop_rate,
                'enable_trailing_stop': self.enable_trailing_stop,
                'trailing_stop_activation_profit_rate': self.trailing_stop_activation_profit_rate,
                'current_stop_price': self.current_stop_price
            }
        }

    @classmethod
    def from_db_dict(cls, data: dict) -> 'OrderExtension':
        extra = data.get('extra_data', {})

        return cls(
            entry_id=data['entry_order_id'],
            side=OrderSide(data['order_side']),
            price=data['entry_price'],
            quantity=data['quantity'],
            fixed_take_profit_rate=extra.get('fixed_take_profit_rate', 0.0),
            signal_min_take_profit_rate=extra.get('signal_min_take_profit_rate', 0.0),
            exit_price=data.get('exit_price'),
            status=data.get('status'),
            exit_id=data.get('exit_order_id'),
            stop_loss_rate=extra.get('stop_loss_rate', 0.0),
            enable_stop_loss=extra.get('enable_stop_loss', False),
            trailing_stop_rate=extra.get('trailing_stop_rate', 0.0),
            enable_trailing_stop=extra.get('enable_trailing_stop', False),
            trailing_stop_activation_profit_rate=extra.get('trailing_stop_activation_profit_rate', 0.0),
            current_stop_price=extra.get('current_stop_price')
        )

    def __hash__(self):
        return hash(self.entry_id)

    def __eq__(self, other: Any):
        if isinstance(other, OrderExtension):
            return self.entry_id == other.entry_id
        return False

    def profit_level(self, current_price: float) -> int:
        compare_fun = self.side.compare_fun()

        if compare_fun(current_price, self._profit(self.fixed_take_profit_rate)):
            return 2
        elif compare_fun(current_price, self._profit(self.signal_min_take_profit_rate)):
            return 1
        elif compare_fun(current_price, self.price):
            return 0

        return -1

    def profit_and_loss_ratio(self, current_price: float) -> float:
        loss_rate = float("{:.6f}".format(abs(current_price - self.price) / self.price))
        if self.profit_level(current_price) < 0:
            return -loss_rate
        else:
            return loss_rate

    def _profit(self, rate: float) -> float:
        rate_base = 1
        if self.side == OrderSide.SELL:
            rate_base = -1
        return self.price * (1 + rate * rate_base)


class OrderExtensionManager:
    """线程安全的订单扩展数据管理器"""

    def __init__(self, strategy_id: str, symbol: str, repository: StrategyRepository | None = None):
        self._extensions: Dict[str, OrderExtension] = {}
        self._lock = threading.RLock()
        self.strategy_id = strategy_id
        self.symbol = symbol
        self._repository = repository or SQLiteStrategyRepository()

    @property
    def extensions(self) -> List[OrderExtension]:
        with self._lock:
            return list(self._extensions.values())

    def add(self, ext: OrderExtension) -> None:
        with self._lock:
            self._extensions[ext.entry_id] = ext

    def get(self, entry_id: str) -> OrderExtension | None:
        with self._lock:
            return self._extensions.get(entry_id)

    def _remove(self, custom_id: str) -> bool:
        with self._lock:
            if custom_id in self._extensions:
                del self._extensions[custom_id]
                return True
            return False

    def load_orders(self, force: bool = False) -> bool:
        with self._lock:
            try:
                db_orders = self._repository.load_active_orders(self.strategy_id)
                if not db_orders:
                    return False
                for order_dict in db_orders:
                    ext = OrderExtension.from_db_dict(order_dict)
                    self.add(ext)
                return True
            except Exception as e:
                logger.error(f"Failed to load orders from database for {self.strategy_id}: {e}")
                return False

    def record_orders(self, closed_extensions: List[OrderExtension] | None = None, refresh_orders: bool = False) -> None:
        if closed_extensions is None:
            closed_extensions = []

        with self._lock:
            for ext in closed_extensions:
                self._remove(ext.entry_id)

            refresh_orders = refresh_orders or bool(closed_extensions)

            for ext in closed_extensions:
                try:
                    profit = 0.0
                    if ext.exit_price and ext.price:
                        direction = 1 if ext.side == OrderSide.BUY else -1
                        profit = (ext.exit_price - ext.price) * ext.quantity * direction

                    self._repository.append_trade_history(
                        strategy_id=self.strategy_id,
                        trade_record={
                            'symbol': self.symbol,
                            'entry_order_id': ext.entry_id,
                            'exit_order_id': ext.exit_id,
                            'entry_price': ext.price,
                            'exit_price': ext.exit_price or 0.0,
                            'quantity': ext.quantity,
                            'profit': profit
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to record trade history for {ext.entry_id}: {e}")

            if refresh_orders:
                try:
                    db_orders = [e.to_db_dict(self.symbol) for e in self.extensions]
                    self._repository.save_active_orders(self.strategy_id, db_orders)
                except Exception as e:
                    logger.error(f"Failed to save active orders for {self.strategy_id}: {e}")


class SignalGridStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: Symbol
    timeframe: str
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    grid_spacing_rate: float = 0.01
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    signal: Signal | None = None

    enable_exit_signal: bool = True
    exit_signal_take_profit_min_rate: float = 0.002

    fixed_rate_take_profit: bool = False
    take_profit_use_limit_order: bool = False
    fixed_take_profit_rate: float = 0.006

    close_position_ratio: float = 1.0

    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN

    order_file_path: str = 'data/grids_strategy_v2.json'

    @field_serializer("signal")
    def serialize_signal(self, signal, _info):
        if signal is None:
            return None
        return signal.__class__.__name__

    position_reverse: bool = False
    enable_max_order_stop_loss: bool = False
    paused_after_stop_loss: bool = True
    enable_order_stop_loss: bool = False
    order_stop_loss_rate: float = 0.05
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.02
    trailing_stop_activation_profit_rate: float = 0.01

@register_strategy("signal_grid", SignalGridStrategyConfig)
class SignalGridStrategy(SimpleStrategy):

    def __init__(self, config: SignalGridStrategyConfig, ex_client: ExSwapClient, repository: StrategyRepository | None = None):
        super().__init__(config.symbol, config.timeframe)
        self.config = config
        self.ex_client = ex_client
        self.strategy_id = f"signal_grid_{self.config.symbol.simple()}_{self.config.position_side.value}_{self.config.master_side.value}"
        self.order_repo = ex_client.order_repo

        self._repository = repository or SQLiteStrategyRepository()

        self._repository.save_strategy_instance(
            strategy_id=self.strategy_id,
            strategy_type="signal_grid",
            symbol=self.config.symbol.simple(),
            config_data=self.config.model_dump_json()
        )

        self.ext_manager = OrderExtensionManager(
            strategy_id=self.strategy_id,
            symbol=self.config.symbol.simple(),
            repository=self._repository
        )
        self.ext_manager.load_orders(True)

        self.on_stop_loss_order_all: Callable[[], None] = lambda: None
        self.close_position: bool = False
        self.is_running: bool = True

    def exchange_client(self) -> ExClient:
        return self.ex_client

    def place_order(self, order_id: str, side: OrderSide, qty: float, price: float, first_price: float | None = None):
        if self.config.position_reverse:
            position_side = PositionSide.SHORT if self.config.position_side == PositionSide.LONG else PositionSide.LONG
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        else:
            position_side = self.config.position_side

        return self.ex_client.place_order_v2(
            strategy_id=self.strategy_id,
            custom_id=order_id,
            symbol=self.config.symbol,
            order_side=side,
            quantity=qty,
            price=price,
            position_side=position_side,
            place_order_behavior=self.config.place_order_behavior,
            first_price=first_price
        )

    def _check_max_order_stop_loss(self) -> bool:
        if self.config.enable_max_order_stop_loss and self.config.max_order - len(self.ext_manager.extensions) <= 1:
            return True
        return False

    def check_open_order(self) -> bool:
        if len(self.ext_manager.extensions) >= self.config.max_order:
            return False

        if self.config.signal:
            if not self.config.signal.is_entry(self.klines_df):
                return False

        if self.latest_kline_obj is None:
            return False

        close_price = self.latest_kline_obj.close
        if close_price < self.config.lowest_price or close_price > self.config.highest_price:
            return False

        extensions = self.ext_manager.extensions
        if extensions:
            recent_price_order = self.config.master_side.extremum_fun()(extensions, key=lambda ext: ext.price)
        else:
            recent_price_order = None

        if (not recent_price_order) or (recent_price_order and recent_price_order.profit_and_loss_ratio(close_price) <= -self.config.grid_spacing_rate):
            if self._check_max_order_stop_loss():
                return False
            order_id = build_order_id(self.config.master_side)
            stop_loss_rate = self.config.order_stop_loss_rate if self.config.enable_order_stop_loss else 0.0
            trailing_stop_rate = self.config.trailing_stop_rate if self.config.enable_trailing_stop else 0.0
            trailing_activation_rate = self.config.trailing_stop_activation_profit_rate if self.config.enable_trailing_stop else 0.0

            current_stop_price = None
            if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
                if self.config.master_side == OrderSide.BUY:
                    current_stop_price = close_price * (1 - stop_loss_rate)
                else:
                    current_stop_price = close_price * (1 + stop_loss_rate)

            ext = OrderExtension(
                entry_id=order_id,
                side=self.config.master_side,
                price=close_price,
                quantity=self.config.per_order_qty,
                fixed_take_profit_rate=self.config.fixed_take_profit_rate,
                signal_min_take_profit_rate=self.config.exit_signal_take_profit_min_rate,
                status=OrderStatus.OPEN.value,
                stop_loss_rate=stop_loss_rate,
                enable_stop_loss=self.config.enable_order_stop_loss,
                trailing_stop_rate=trailing_stop_rate,
                enable_trailing_stop=self.config.enable_trailing_stop,
                trailing_stop_activation_profit_rate=trailing_activation_rate,
                current_stop_price=current_stop_price
            )

            if self.config.per_order_qty == 0:
                ext.status = OrderStatus.CLOSED.value
            else:
                entry_order_result = self.place_order(order_id, self.config.master_side, self.config.per_order_qty, close_price, first_price=close_price)
                if entry_order_result:
                    ext.entry_id = entry_order_result.order_id
                    ext.price = entry_order_result.price or close_price
                    ext.status = entry_order_result.status.value
            self.ext_manager.add(ext)
            return True
        return False

    def check_close_order(self) -> List[OrderExtension]:
        if self.latest_kline_obj is None:
            return []

        current_extensions = self.ext_manager.extensions
        exit_signal = self.config.enable_exit_signal and self.config.signal and self.config.signal.is_exit(self.klines_df)

        remove_extensions: List[OrderExtension] = []
        exit_extensions: List[OrderExtension] = []
        exit_qty = 0
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for ext in current_extensions:
            profit_level = ext.profit_level(self.latest_kline_obj.close)

            stop_loss_triggered = False
            if ext.enable_stop_loss and ext.current_stop_price is not None:
                if ext.side == OrderSide.BUY:
                    stop_loss_triggered = self.latest_kline_obj.close <= ext.current_stop_price
                else:
                    stop_loss_triggered = self.latest_kline_obj.close >= ext.current_stop_price

            if stop_loss_order_all or (profit_level == 2 and self.config.fixed_rate_take_profit) or (profit_level == 1 and exit_signal) or stop_loss_triggered:
                if OrderStatus.is_open(ext.status):
                    entry_order_query_result = self.ex_client.query_order(ext.entry_id, self.config.symbol)
                    ext.status = OrderStatus.EXPIRED.value if entry_order_query_result is None else entry_order_query_result.status.value
                    if not OrderStatus.is_closed(ext.status):
                        remove_extensions.append(ext)
                        if OrderStatus.is_open(ext.status):
                            self.ex_client.cancel(ext.entry_id, self.config.symbol)
                        continue

                if ext.exit_id and ext.exit_price:
                    exit_order_query_result = self.ex_client.query_order(ext.exit_id, self.config.symbol)
                    if exit_order_query_result:
                        exit_status = exit_order_query_result.status.value
                        if OrderStatus.is_closed(exit_status):
                            remove_extensions.append(ext)
                            continue
                        elif OrderStatus.is_open(exit_status):
                            self.ex_client.cancel(ext.exit_id, self.config.symbol)

                exit_qty += ext.quantity
                ext.exit_price = self.latest_kline_obj.close
                exit_extensions.append(ext)

        if exit_qty > 0:
            exit_order_side = self.config.master_side.reversal()
            exit_order_id = build_order_id(exit_order_side)
            actual_exit_qty = exit_qty * self.config.close_position_ratio
            execute_exit_order_result = self.place_order(exit_order_id, exit_order_side, actual_exit_qty, self.latest_kline_obj.close)
            if execute_exit_order_result:
                for ext in exit_extensions:
                    ext.exit_id = execute_exit_order_result.order_id
                    ext.exit_price = execute_exit_order_result.price or ext.exit_price

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return exit_extensions + remove_extensions

    def _on_kline_finished(self):
        if not self.is_running or self.latest_kline_obj is None:
            return

        refresh = self.ext_manager.load_orders()

        extremum_price = self.latest_kline_obj.high if self.config.master_side == OrderSide.BUY else self.latest_kline_obj.low
        current_extensions = self.ext_manager.extensions
        for ext in current_extensions:
            if not ext.enable_trailing_stop or ext.current_stop_price is None:
                continue

            activation_price = ext.price * (1 + ext.trailing_stop_activation_profit_rate * ext.side.to_int())
            if ext.side.compare_fun(and_eq=True)(extremum_price, activation_price):
                new_stop_price = extremum_price * (1 - ext.trailing_stop_rate * ext.side.to_int())
                ext.current_stop_price = ext.side.reversal().extremum_fun()(ext.current_stop_price, new_stop_price)
                refresh = True

        if not self.check_open_order():
            closed_extensions = self.check_close_order()
        else:
            closed_extensions = []
            refresh = True

        self.ext_manager.record_orders(closed_extensions, refresh)

    def _on_kline(self):
        if self.latest_kline_obj is None:
            return

        extensions_to_process = self.ext_manager.extensions
        refresh_orders = False

        closed_extensions = []
        if self.config.fixed_rate_take_profit and self.config.take_profit_use_limit_order:
            for ext in extensions_to_process:
                if ext.quantity == 0:
                    continue

                if ext.exit_id and ext.exit_price:
                    if self.latest_kline_obj.low <= ext.exit_price <= self.latest_kline_obj.high:
                        exit_order_query_result = self.ex_client.query_order(ext.exit_id, self.config.symbol)
                        if exit_order_query_result:
                            exit_status = exit_order_query_result.status.value
                            if OrderStatus.is_closed(exit_status):
                                closed_extensions.append(ext)
                else:
                    if OrderStatus.is_open(ext.status):
                        entry_order_query_result = self.ex_client.query_order(ext.entry_id, self.config.symbol)
                        if entry_order_query_result:
                            ext.status = entry_order_query_result.status.value

                    if OrderStatus.is_closed(ext.status):
                        exit_order_side = self.config.master_side.reversal()
                        exit_order_id = build_order_id(exit_order_side)
                        exit_price = ext.price * (1 + self.config.master_side.to_int() * self.config.fixed_take_profit_rate)
                        exit_qty = ext.quantity * self.config.close_position_ratio

                        exit_order_result = self.place_order(exit_order_id, exit_order_side, exit_qty, exit_price, first_price=exit_price)
                        if exit_order_result:
                            ext.exit_id = exit_order_result.order_id
                            ext.exit_price = exit_price
                            refresh_orders = True

            self.ext_manager.record_orders(closed_extensions, refresh_orders=refresh_orders)
