import os
import secrets
from typing import Optional, Dict, Any
import numpy as np
from pydantic import BaseModel

from strategy import MultiTimeframeStrategy
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from client.ex_client import ExSwapClient
from model import OrderSide, PositionSide, PlaceOrderBehavior, Symbol, OrderStatus
from utils.json_util import dump_file, loads
import log

logger = log.getLogger(__name__)


class AlphaTrendPosition(BaseModel):
    """AlphaTrend position tracking"""
    position_side: PositionSide
    entry_price: float
    quantity: float
    stop_loss_price: float
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    alpha_trend_value_at_entry: float = 0.0
    highest_price_since_entry: float = 0.0
    exit_mode: bool = False  # True when price moved >2% away from alpha_trend_value


class AlphaTrendStrategyConfig(BaseModel):
    """AlphaTrend strategy configuration"""
    symbol: Symbol
    timeframes: list[str] = ["15m", "5m"]  # List of timeframes, ordered from main to auxiliary (higher to lower)
    position_size: float = 0.01  # Base position size per trade
    stop_loss_rate: float = 0.03  # 3% fixed stop loss
    distance_threshold: float = 0.02  # 2% distance threshold for exit mode
    atr_multiple: float = 1.0  # AlphaTrend ATR multiplier
    period: int = 8  # AlphaTrend period
    signal_reverse: bool = False  # Reverse the signal direction
    enable_short_trades: bool = True  # Allow short positions
    enable_long_trades: bool = True  # Allow long positions
    backup_file_path: str = "data/alpha_trend_strategy_state.json"  # Path to store strategy state


class AlphaTrendStrategy(MultiTimeframeStrategy):
    def __init__(self, ex_client: ExSwapClient, config: AlphaTrendStrategyConfig):
        super().__init__()
        self.config = config
        self.ex_client = ex_client

        # Validate timeframes configuration
        if not self.config.timeframes or len(self.config.timeframes) < 2:
            raise ValueError("At least 2 timeframes must be configured (main and auxiliary)")

        # Add required timeframes
        for timeframe in self.config.timeframes:
            self.add_timeframe(timeframe)

        # Initialize signals for different timeframes
        self.signals: Dict[str, AlphaTrendSignal] = {}
        for timeframe in self.config.timeframes:
            self.signals[timeframe] = AlphaTrendSignal(
                OrderSide.BUY,
                self.config.atr_multiple,
                self.config.period,
                reverse=self.config.signal_reverse
            )

        # Position tracking
        self.position: Optional[AlphaTrendPosition] = None

        # Current monitoring timeframe index (starts with main timeframe)
        self.current_monitor_timeframe_index: int = 0

        # Performance tracking
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.total_pnl: float = 0.0

        # Load previous state if exists
        self._load_state()

        logger.info(f"AlphaTrendStrategy initialized for {config.symbol.binance()} with timeframes: {self.config.timeframes}")

    def _can_open_position(self, position_side: PositionSide) -> bool:
        """Check if we can open a new position"""
        if self.position is not None:
            return False

        if position_side == PositionSide.LONG and self.config.enable_long_trades:
            return True

        if position_side == PositionSide.SHORT and self.config.enable_short_trades:
            return True

        return False

    def _generate_order_id(self, side: OrderSide) -> str:
        """Generate unique order ID"""
        return f"{side.value}{secrets.token_hex(nbytes=5)}"

    def _open_position(self, position_side: PositionSide, entry_price: float, alpha_trend_value: float) -> Optional[str]:
        """Open a new position"""
        if not self._can_open_position(position_side):
            return None

        # Calculate stop loss price
        if position_side == PositionSide.LONG:
            stop_loss_price = entry_price * (1 - self.config.stop_loss_rate)
        else:  # SHORT
            stop_loss_price = entry_price * (1 + self.config.stop_loss_rate)

        # Create position tracking
        self.position = AlphaTrendPosition(
            position_side=position_side,
            entry_price=entry_price,
            quantity=self.config.position_size,
            stop_loss_price=stop_loss_price,
            alpha_trend_value_at_entry=alpha_trend_value,
            highest_price_since_entry=entry_price,
            exit_mode=False
        )

        try:
            # Generate order ID
            order_side = OrderSide.BUY if position_side == PositionSide.LONG else OrderSide.SELL
            order_id = self._generate_order_id(order_side)

            # Place market order
            order_result = self.ex_client.place_order_v2(
                custom_id=order_id,
                symbol=self.config.symbol,
                order_side=order_side,
                quantity=self.config.position_size,
                price=entry_price,
                position_side=position_side,
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN
            )

            if order_result and order_result.get('clientOrderId'):
                self.position.entry_order_id = order_result['clientOrderId']

                # Save state after opening position
                self._save_state()

                logger.info(f"Opened {position_side.value} position: {self.config.position_size} @ {entry_price} (ID: {order_result['clientOrderId']})")
                return order_result['clientOrderId']

        except Exception as e:
            logger.error(f"Failed to open {position_side.value} position: {e}")
            self.position = None

        return None

    def _close_position(self, exit_price: float, reason: str) -> bool:
        """Close the current position"""
        if not self.position or not self.position.entry_order_id:
            return False

        # Determine exit order side and position side
        if self.position.position_side == PositionSide.LONG:
            exit_order_side = OrderSide.SELL
            exit_position_side = PositionSide.LONG
        else:
            exit_order_side = OrderSide.BUY
            exit_position_side = PositionSide.SHORT

        try:
            # Verify if entry order was filled
            if self.position.entry_order_id:
                query_result = self.ex_client.query_order(self.position.entry_order_id, self.config.symbol)
                if not query_result or not OrderStatus.is_closed(query_result.get('status')):
                    # Entry order not filled, cancel it
                    cancel_result = self.ex_client.cancel(self.position.entry_order_id, self.config.symbol)
                    if cancel_result:
                        logger.info(f"Canceled unfilled entry order: {self.position.entry_order_id} ({reason})")
                        self.position = None
                        self._save_state()
                        return True
                    else:
                        logger.error(f"Failed to cancel unfilled entry order: {self.position.entry_order_id}")
                        return False
        except Exception as e:
            logger.error(f"Failed to query entry order {self.position.entry_order_id}: {e}")
            return False

        try:
            # Generate exit order ID
            exit_order_id = self._generate_order_id(exit_order_side)

            # Place exit order
            order_result = self.ex_client.place_order_v2(
                custom_id=exit_order_id,
                symbol=self.config.symbol,
                order_side=exit_order_side,
                quantity=self.position.quantity,
                price=exit_price,
                position_side=exit_position_side,
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN
            )

            if order_result and order_result.get('clientOrderId'):
                self.position.exit_order_id = order_result['clientOrderId']

                # Calculate realized P&L
                if self.position and self.position.position_side == PositionSide.LONG:
                    realized_pnl = (exit_price - self.position.entry_price) * self.position.quantity
                elif self.position:
                    realized_pnl = (self.position.entry_price - exit_price) * self.position.quantity
                else:
                    realized_pnl = 0.0

                self.total_pnl += realized_pnl
                self.total_trades += 1

                if realized_pnl > 0:
                    self.winning_trades += 1

                if self.position:
                    logger.info(f"Closed {self.position.position_side.value} position: {realized_pnl:.4f} P&L ({reason})")

                # Clear position
                self.position = None
                self._save_state()
                return True

        except Exception as e:
            logger.error(f"Failed to close {self.position.position_side.value} position: {e}")

        return False

    def _check_entry_signals(self):
        """Check for entry signals on main timeframe"""
        main_timeframe = self.config.timeframes[0]
        df = self.klines_to_dataframe(main_timeframe)
        if df.empty or len(df) < 10:
            return

        current_kline = self.get_last_kline(main_timeframe)
        if not current_kline:
            return

        current_price = current_kline.close if current_kline.close is not None else 0

        # Get current alpha_trend_value
        signal_instance = self.signals[main_timeframe]
        current_signal_status = signal_instance.run(df)
        current_alpha_trend_value = signal_instance.current_alpha_trend

        if current_signal_status == 1 and self._can_open_position(PositionSide.LONG):
            self._open_position(PositionSide.LONG, current_price, current_alpha_trend_value)
        elif current_signal_status == -1 and self._can_open_position(PositionSide.SHORT):
            self._open_position(PositionSide.SHORT, current_price, current_alpha_trend_value)

    def _monitor_position_on_timeframe(self, timeframe: str):
        """Monitor existing position on specified timeframe"""
        if not self.position:
            return

        df = self.klines_to_dataframe(timeframe)
        if df.empty:
            return

        current_kline = self.get_last_kline(timeframe)
        if not current_kline:
            return

        current_price = current_kline.close if current_kline.close is not None else 0
        current_high = current_kline.high if current_kline.high is not None else 0

        # Update highest price since entry
        self.position.highest_price_since_entry = max(self.position.highest_price_since_entry, current_high)

        # Get current alpha_trend_value
        current_alpha_trend = df.iloc[-1]['alpha_trend'] if 'alpha_trend' in df.columns else 0

        # Check if we should enter exit mode or switch to lower timeframe
        if current_alpha_trend != 0:
            distance = abs(self.position.highest_price_since_entry - current_alpha_trend) / current_alpha_trend

            # Check if we should enter exit mode or switch timeframe
            if not self.position.exit_mode and distance > self.config.distance_threshold:
                self.position.exit_mode = True
                logger.info(f"Entered exit mode on {timeframe}: highest_price={self.position.highest_price_since_entry:.4f}, "
                          f"alpha_trend={current_alpha_trend:.4f}, distance={distance:.3f}")

                # If this is not the last timeframe, switch to next lower timeframe
                if self.current_monitor_timeframe_index < len(self.config.timeframes) - 1:
                    self.current_monitor_timeframe_index += 1
                    next_timeframe = self.config.timeframes[self.current_monitor_timeframe_index]
                    logger.info(f"Switching monitoring to lower timeframe: {next_timeframe}")
                else:
                    logger.info(f"Already on lowest timeframe: {timeframe}")

        # Check stop loss
        if self._should_stop_loss(current_price):
            self._close_position(current_price, "stop_loss")
            # Reset monitoring timeframe when position is closed
            self.current_monitor_timeframe_index = 0
            return

        # Check normal exit (only on main timeframe when not in exit mode)
        if not self.position.exit_mode and timeframe == self.config.timeframes[0] and self._should_normal_exit(df, current_price):
            self._close_position(current_price, "normal_exit")
            # Reset monitoring timeframe when position is closed
            self.current_monitor_timeframe_index = 0
            return

        # Check exit signal (in exit mode, on current monitoring timeframe)
        if self.position.exit_mode and timeframe == self.config.timeframes[self.current_monitor_timeframe_index]:
            if len(df) >= 10 and self.signals[timeframe].is_exit(df):
                self._close_position(current_price, f"exit_mode_signal_{timeframe}")
                # Reset monitoring timeframe when position is closed
                self.current_monitor_timeframe_index = 0

    def _should_stop_loss(self, current_price: float) -> bool:
        """Check if stop loss should trigger"""
        if not self.position:
            return False

        if self.position.position_side == PositionSide.LONG:
            return current_price <= self.position.stop_loss_price
        else:  # SHORT
            return current_price >= self.position.stop_loss_price

    def _should_normal_exit(self, df_15m, current_price: float) -> bool:
        """Check if we should exit in normal mode (price falling back to alpha_trend)"""
        if not self.position or self.position.exit_mode:
            return False

        # Get current alpha_trend_value
        current_alpha_trend = df_15m.iloc[-1]['alpha_trend'] if 'alpha_trend' in df_15m.columns else 0

        if current_alpha_trend == 0:
            return False

        # For long positions, exit when price falls back and crosses below alpha_trend
        if self.position.position_side == PositionSide.LONG:
            prev_alpha_trend = df_15m.iloc[-2]['alpha_trend'] if len(df_15m) > 1 and 'alpha_trend' in df_15m.columns else current_alpha_trend
            # Exit if close crosses below current alpha_trend (price falling back)
            return current_price < current_alpha_trend and df_15m.iloc[-2]['close'] >= prev_alpha_trend

        # For short positions, exit when price rises back and crosses above alpha_trend
        else:
            prev_alpha_trend = df_15m.iloc[-2]['alpha_trend'] if len(df_15m) > 1 and 'alpha_trend' in df_15m.columns else current_alpha_trend
            # Exit if close crosses above current alpha_trend (price rising back)
            return current_price > current_alpha_trend and df_15m.iloc[-2]['close'] <= prev_alpha_trend

    def on_kline_finished(self, timeframe: Optional[str] = None):
        """Main strategy logic - called when K-line is finished"""
        if not timeframe or timeframe not in self.config.timeframes:
            return

        # Check for entry signals on main timeframe
        if timeframe == self.config.timeframes[0] and not self.position:
            self._check_entry_signals()

        # Monitor existing position on this timeframe
        if self.position:
            self._monitor_position_on_timeframe(timeframe)

    def _get_backup_file_path(self) -> str:
        """Get the backup file path for this strategy instance"""
        return self.config.backup_file_path

    def _save_state(self):
        """Save current strategy state to file"""
        try:
            state = {
                'position': self.position.dict() if self.position else None,
                'current_monitor_timeframe_index': self.current_monitor_timeframe_index,
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'total_pnl': self.total_pnl
            }

            backup_path = self._get_backup_file_path()
            if not os.path.exists(backup_path):
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)

            dump_file(state, backup_path)
            logger.debug(f"Strategy state saved to {backup_path}")

        except Exception as e:
            logger.error(f"Failed to save strategy state: {e}")

    def _load_state(self):
        """Load strategy state from file if exists"""
        backup_path = self._get_backup_file_path()
        if not os.path.exists(backup_path):
            logger.info(f"No backup file found at {backup_path}, starting fresh")
            return

        try:
            with open(backup_path, 'r') as f:
                state_data = loads(f.read())

            # Restore position
            if state_data.get('position'):
                self.position = AlphaTrendPosition(**state_data['position'])

            self.current_monitor_timeframe_index = state_data.get('current_monitor_timeframe_index', 0)
            self.total_trades = state_data.get('total_trades', 0)
            self.winning_trades = state_data.get('winning_trades', 0)
            self.total_pnl = state_data.get('total_pnl', 0.0)

            logger.info(f"Strategy state loaded from {backup_path}: "
                       f"position={self.position is not None}, "
                       f"monitor_timeframe_index={self.current_monitor_timeframe_index}, "
                       f"{self.total_trades} trades, PNL: {self.total_pnl:.4f}")

        except Exception as e:
            logger.error(f"Failed to load strategy state from {backup_path}: {e}")
            # Continue with fresh state if loading fails

