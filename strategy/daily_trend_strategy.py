import logging
import talib
from typing import List, Optional

from model import Symbol, OrderSide, PositionSide, OrderStatus, PlaceOrderBehavior
from strategy import GeneralStrategy, Signal
from pydantic import BaseModel, ConfigDict
from strategy.signal_grid_strategy import Order, OrderManager, build_order_id

logger = logging.getLogger(__name__)

class DailyTrendStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    trade_symbol: Symbol
    trade_timeframe: str
    direction_symbols: List[Symbol]
    per_order_qty: float
    max_daily_orders: int = 3
    min_tp_rate: float = 0.02
    max_tp_rate: float = 0.03
    atr_tp_multiplier: float = 1.5
    stop_loss_rate: float = 0.03
    order_file_path: str
    signal: Signal


class DailyTrendStrategy(GeneralStrategy):
    def __init__(self, config: DailyTrendStrategyConfig, ex_client):
        super().__init__([config.trade_symbol] + config.direction_symbols, ['1d', config.trade_timeframe])
        self.config = config
        self._ex_client = ex_client
        self.order_manager = OrderManager(order_file_path=config.order_file_path)
        self.order_manager.load_orders(True)

        self.current_direction: OrderSide | None = None
        self.daily_order_count: int = 0

        self._finished_1d_symbols = set()
        self._direction_initialized = False

    def exchange_client(self):
        return self._ex_client

    def on_kline(self, timeframe: str, symbol: Symbol):
        if timeframe == self.config.trade_timeframe and symbol == self.config.trade_symbol:
            self._check_close()

    def on_kline_finished(self, timeframe: str, symbol: Symbol):
        if timeframe == '1d':
            self._finished_1d_symbols.add(symbol)

            if self._finished_1d_symbols.issuperset(self.config.direction_symbols):
                self._on_daily_close()
                self._finished_1d_symbols.clear()

        elif timeframe == self.config.trade_timeframe:
            if symbol == self.config.trade_symbol:
                self._on_trade_kline_finished()

    def _ensure_direction_initialized(self):
        if self._direction_initialized:
            return

        # 检查是否所有 direction_symbols 的 1d 数据都已经有了
        if set(self.config.direction_symbols).issubset(self.kline_data_dict.keys()):
            for sym in self.config.direction_symbols:
                df = self.klines('1d', sym)
                if len(df) == 0:
                    return

            self.current_direction = self._vote_direction()
            self._direction_initialized = True
            logger.info(f"Initialized daily direction: {self.current_direction}")

    def _vote_direction(self) -> Optional[OrderSide]:
        votes = 0
        target_count = len(self.config.direction_symbols)

        for sym in self.config.direction_symbols:
            df = self.klines('1d', sym)

            if len(df) == 0 or 'finished' not in df.columns:
                logger.warning(f"No valid 1d kline found for {sym}")
                return None

            # Get only finished klines
            sym_df = df[df['finished'] == True]
            if len(sym_df) == 0:
                logger.warning(f"No finished 1d kline found for {sym}")
                return None

            last_row = sym_df.iloc[-1]
            if last_row['close'] > last_row['open']:
                votes += 1
            elif last_row['close'] < last_row['open']:
                votes -= 1

        if votes == target_count:
            return OrderSide.BUY
        elif votes == -target_count:
            return OrderSide.SELL
        else:
            logger.info(f"Direction mixed or unchanged (votes: {votes}), keeping neutral")
            return None

    def _on_daily_close(self):
        self._force_close_all()
        self.current_direction = self._vote_direction()
        self.daily_order_count = 0
        logger.info(f"Daily Close. New direction: {self.current_direction}. Order count reset to 0.")

    def _force_close_all(self):
        orders = self.order_manager.orders
        if not orders:
            return

        logger.info(f"Daily Close UTC0: Force closing all {len(orders)} open positions for {self.config.trade_symbol.simple()}.")

        remove_orders = []
        exit_qty = 0

        # 必须取 DOGE 的当前价格来挂单
        kline = self.latest_kline(self.config.trade_timeframe, self.config.trade_symbol)
        close_price = kline.close if kline else 0.0

        for order in orders:
            if OrderStatus.is_open(order.status):
                self._ex_client.cancel(order.entry_id, self.config.trade_symbol)
            exit_qty += order.quantity
            remove_orders.append(order)

        if exit_qty > 0 and close_price > 0:
            first_order = remove_orders[0]
            exit_order_side = first_order.side.reversal()
            position_side = PositionSide.LONG if first_order.side == OrderSide.BUY else PositionSide.SHORT

            exit_id = build_order_id(exit_order_side)

            res = self._ex_client.place_order_v2(
                custom_id=exit_id,
                symbol=self.config.trade_symbol,
                order_side=exit_order_side,
                quantity=exit_qty,
                position_side=position_side,
                price=close_price,
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN
            )
            if res:
                for o in remove_orders:
                    o.exit_id = res.get('clientOrderId', exit_id)
                    o.exit_price = res.get('price', close_price)

        self.order_manager.record_orders(closed_orders=remove_orders, refresh_orders=True)

    def _on_trade_kline_finished(self):
        self._ensure_direction_initialized()
        self._check_close()

        if self.current_direction is None:
            return

        if self.daily_order_count >= self.config.max_daily_orders:
            return

        df = self.klines(self.config.trade_timeframe, self.config.trade_symbol)
        if self.config.signal.is_entry(df):
            self._open_order()
            self.daily_order_count += 1

        self._check_close()

    def _open_order(self):
        kline = self.latest_kline(self.config.trade_timeframe, self.config.trade_symbol)
        if not kline: return

        df = self.klines(self.config.trade_timeframe, self.config.trade_symbol)
        if len(df) < 15:
            logger.warning("Not enough kline data to calculate ATR")
            return

        order_side = self.current_direction
        if order_side is None:
            return

        position_side = PositionSide.LONG if order_side == OrderSide.BUY else PositionSide.SHORT

        order_id = build_order_id(order_side)
        price = kline.close

        # Calculate dynamic take profit rate based on ATR
        atr_series = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
        current_atr = atr_series[-1]
        atr_rate = current_atr / price
        dynamic_tp_rate = atr_rate * self.config.atr_tp_multiplier
        final_tp_rate = max(self.config.min_tp_rate, min(self.config.max_tp_rate, dynamic_tp_rate))

        logger.info(f"Dynamic TP Rate calculation: ATR Rate={atr_rate*100:.2f}%, Expected TP Rate={dynamic_tp_rate*100:.2f}%, Final TP Rate={final_tp_rate*100:.2f}%")

        logger.info(f"Opening {order_side} position for {self.config.trade_symbol.simple()} at {price}")

        res = self._ex_client.place_order_v2(
            custom_id=order_id,
            symbol=self.config.trade_symbol,
            order_side=order_side,
            quantity=self.config.per_order_qty,
            position_side=position_side,
            price=price,
            place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
            first_price=price
        )

        order = Order(
            entry_id=order_id,
            side=order_side,
            price=price,
            quantity=self.config.per_order_qty,
            fixed_take_profit_rate=final_tp_rate,
            signal_min_take_profit_rate=final_tp_rate,
            status=OrderStatus.OPEN.value,
            enable_stop_loss=True,
            stop_loss_rate=self.config.stop_loss_rate
        )

        if res and res.get('clientOrderId'):
            order.entry_id = res['clientOrderId']
            order.price = res.get('price', price)
            order.status = res.get('status', OrderStatus.OPEN.value)

        self.order_manager.add_order(order)
        self.order_manager.record_orders(refresh_orders=True)

    def _check_close(self):
        kline = self.latest_kline(self.config.trade_timeframe, self.config.trade_symbol)
        if not kline: return

        current_price = kline.close
        orders = self.order_manager.orders
        if not orders:
            return

        remove_orders = []
        exit_qty = 0

        for order in orders:
            loss_rate = order.profit_and_loss_ratio(current_price)

            hit_tp = loss_rate > 0 and abs(loss_rate) >= order.fixed_take_profit_rate
            hit_sl = loss_rate < 0 and abs(loss_rate) >= self.config.stop_loss_rate

            if hit_tp or hit_sl:
                logger.info(f"Closing position {order.entry_id}. PnL: {loss_rate*100:.2f}%")
                if OrderStatus.is_open(order.status):
                    self._ex_client.cancel(order.entry_id, self.config.trade_symbol)

                exit_qty += order.quantity
                order.exit_price = current_price
                remove_orders.append(order)

        if exit_qty > 0:
            first_order = remove_orders[0]
            exit_order_side = first_order.side.reversal()
            position_side = PositionSide.LONG if first_order.side == OrderSide.BUY else PositionSide.SHORT

            exit_id = build_order_id(exit_order_side)
            res = self._ex_client.place_order_v2(
                custom_id=exit_id,
                symbol=self.config.trade_symbol,
                order_side=exit_order_side,
                quantity=exit_qty,
                position_side=position_side,
                price=current_price,
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
                first_price=current_price
            )
            if res:
                for o in remove_orders:
                    o.exit_id = res.get('clientOrderId', exit_id)
                    o.exit_price = res.get('price', current_price)

            self.order_manager.record_orders(closed_orders=remove_orders, refresh_orders=True)
