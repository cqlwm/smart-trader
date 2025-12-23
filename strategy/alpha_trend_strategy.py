import os
import secrets
from typing import Optional, Dict
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
    extremum_price_since_entry: float = 0.0
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
    # MACD parameters for early profit taking
    macd_fast_period: int = 12  # MACD fast EMA period
    macd_slow_period: int = 26  # MACD slow EMA period
    macd_signal_period: int = 9  # MACD signal line period


class AlphaTrendStrategy(MultiTimeframeStrategy):
    def __init__(self, ex_client: ExSwapClient, config: AlphaTrendStrategyConfig):
        # Validate timeframes configuration
        if not config.timeframes or len(config.timeframes) < 2:
            raise ValueError("At least 2 timeframes must be configured (main and auxiliary)")

        super().__init__(config.timeframes)
        self.config = config
        self.ex_client = ex_client

        # Initialize signals for different timeframes
        self.signals: Dict[str, AlphaTrendSignal] = {}
        for timeframe in self.config.timeframes:
            self.signals[timeframe] = AlphaTrendSignal(
                OrderSide.BUY,
                self.config.atr_multiple,
                self.config.period,
                reverse=self.config.signal_reverse,
                macd_fast_period=self.config.macd_fast_period,
                macd_slow_period=self.config.macd_slow_period,
                macd_signal_period=self.config.macd_signal_period
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


    def is_default_timeframe(self, timeframe: str) -> bool:
        """Check if the timeframe is the default timeframe"""
        return timeframe == self.config.timeframes[0]

    def is_monitoring_timeframe(self, timeframe: str) -> bool:
        """Check if the timeframe is the current monitoring timeframe"""
        return timeframe == self.config.timeframes[self.current_monitor_timeframe_index]

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
            extremum_price_since_entry=entry_price,
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
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
                first_price=entry_price
            )

            if order_result and order_result.get('clientOrderId'):
                self.position.entry_order_id = order_result['clientOrderId']

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
            exit_order_id = self._generate_order_id(exit_order_side)

            order_result = self.ex_client.place_order_v2(
                custom_id=exit_order_id,
                symbol=self.config.symbol,
                order_side=exit_order_side,
                quantity=self.position.quantity,
                price=exit_price,
                position_side=exit_position_side,
                place_order_behavior=PlaceOrderBehavior.CHASER_OPEN,
                first_price=exit_price
            )

            if order_result and order_result.get('clientOrderId'):
                self.position.exit_order_id = order_result['clientOrderId']

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

                self.position = None
                self._save_state()
                return True

        except Exception as e:
            logger.error(f"Failed to close {self.position} position: {e}")

        return False

    def _check_entry_signals(self):
        """Check for entry signals on main timeframe"""
        main_timeframe = self.config.timeframes[0]
        df = self.klines(main_timeframe)
        if df.empty or len(df) < 10:
            return

        current_kline = self.latest_kline(main_timeframe)
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

        df = self.klines(timeframe)
        if df.empty:
            return

        current_kline = self.latest_kline(timeframe)
        if not current_kline:
            return

        if self.position.position_side == PositionSide.LONG:
            self.position.extremum_price_since_entry = max(self.position.extremum_price_since_entry, current_kline.high)
        elif self.position.position_side == PositionSide.SHORT:
            self.position.extremum_price_since_entry = min(self.position.extremum_price_since_entry, current_kline.low)

        signal_instance = self.signals[timeframe]
        signal_instance.run(df)
        current_alpha_trend_value = signal_instance.current_alpha_trend

        # 任何周期都可以进行阈值检查
        # 但只有默认时间框架才可以在退出模式下进行切换
        if current_alpha_trend_value > 0:
            distance = abs(self.position.extremum_price_since_entry - current_alpha_trend_value) / current_alpha_trend_value

            if distance > self.config.distance_threshold:
                if not self.position.exit_mode:
                    self.position.exit_mode = True
                    logger.info(f"Entered exit mode on {timeframe}: extremum_price={self.position.extremum_price_since_entry}, "
                            f"alpha_trend={current_alpha_trend_value}, distance={distance:.4f}")

                    if self.current_monitor_timeframe_index < len(self.config.timeframes) - 1:
                        self.current_monitor_timeframe_index += 1
                        next_timeframe = self.config.timeframes[self.current_monitor_timeframe_index]
                        logger.info(f"Switching monitoring to lower timeframe: {next_timeframe}")
                    else:
                        logger.info(f"Already on lowest timeframe: {timeframe}")


        current_price = current_kline.close

        exit_reason: str | None = None
        # 检查止损
        if self._should_stop_loss(current_price):
            exit_reason = "stop_loss"
        elif self.is_default_timeframe(timeframe):
            if self._should_normal_exit(signal_instance, current_price):
                exit_reason = "normal_exit"
            elif self._should_exit_on_macd_crossover(signal_instance, current_price):
                exit_reason = "macd_crossover_early_profit_taking"
        elif self.is_monitoring_timeframe(timeframe) and self._should_exit_on_distance(signal_instance, current_price):
            exit_reason = f"exit_mode_signal_{timeframe}"

        if exit_reason:
            self._close_position(current_price, exit_reason)
            self.current_monitor_timeframe_index = 0

    def _should_stop_loss(self, current_price: float) -> bool:
        """Check if stop loss should trigger"""
        if not self.position:
            return False

        if self.position.position_side == PositionSide.LONG:
            return current_price <= self.position.stop_loss_price
        else:
            return current_price >= self.position.stop_loss_price

    def _should_normal_exit(self, signal: AlphaTrendSignal, current_price: float) -> bool:
        """Check if we should exit in normal mode (price falling back to alpha_trend)"""
        if not self.position or self.position.exit_mode:
            return False
        
        current_signal_status = signal.current_kline_status

        return self.position.position_side == PositionSide.LONG and current_signal_status == -1 or \
            self.position.position_side == PositionSide.SHORT and current_signal_status == 1

    def _should_exit_on_distance(self, signal: AlphaTrendSignal, current_price: float) -> bool:
        """Check if we should exit based on distance threshold"""
        if not (self.position and self.position.exit_mode):
            return False
        
        current_signal_status = signal.current_kline_status
        current_alpha_trend_value = signal.current_alpha_trend

        if self.position.position_side == PositionSide.LONG:
            return current_price < current_alpha_trend_value or current_signal_status == -1
        else:
            return current_price > current_alpha_trend_value or current_signal_status == 1

    def _is_position_profitable(self, current_price: float) -> bool:
        """Check if the current position is in profit"""
        if not self.position:
            return False

        if self.position.position_side == PositionSide.LONG:
            return current_price > self.position.entry_price
        else:  # SHORT
            return current_price < self.position.entry_price

    def _should_exit_on_macd_crossover(self, signal: AlphaTrendSignal, current_price: float) -> bool:
        """Check if we should exit based on MACD crossover in profitable positions"""
        if not self.position:
            return False

        # Only apply MACD exit logic to profitable positions
        if not self._is_position_profitable(current_price):
            return False

        # Check for MACD crossover in opposite direction to trend
        # Dead cross (MACD falls below signal) during uptrend = exit long
        # Golden cross (MACD rises above signal) during downtrend = exit short

        # Need at least 2 data points to detect crossover
        if self.position.position_side == PositionSide.LONG:
            # For long positions: dead cross during uptrend
            return (signal.previous_macd > signal.previous_macd_signal and
                    signal.current_macd < signal.current_macd_signal)
        else:  # SHORT
            # For short positions: golden cross during downtrend
            return (signal.previous_macd < signal.previous_macd_signal and
                    signal.current_macd > signal.current_macd_signal)

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
                'position': self.position.model_dump() if self.position else None,
                'current_monitor_timeframe_index': self.current_monitor_timeframe_index,
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'total_pnl': self.total_pnl
            }

            backup_path = self._get_backup_file_path()
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
