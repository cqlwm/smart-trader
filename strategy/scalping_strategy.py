import threading
import secrets
import os
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from strategy import StrategyV2
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from client.ex_client import ExSwapClient
from model import OrderSide, PositionSide, PlaceOrderBehavior, Symbol
from utils.json_util import dump_file, loads
import log

logger = log.getLogger(__name__)


class ScalpPosition(BaseModel):
    """Scalping position tracking"""
    position_side: PositionSide
    entry_price: float
    quantity: float
    stop_loss_price: float
    take_profit_price: float
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    unrealized_pnl: float = 0.0

    def update_pnl(self, current_price: float) -> float:
        """Update and return unrealized P&L"""
        if self.position_side == PositionSide.LONG:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:  # SHORT
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        return self.unrealized_pnl

    def should_stop_loss(self, current_price: float) -> bool:
        """Check if stop loss should trigger"""
        if self.position_side == PositionSide.LONG:
            return current_price <= self.stop_loss_price
        else:  # SHORT
            return current_price >= self.stop_loss_price

    def should_take_profit(self, current_price: float) -> bool:
        """Check if take profit should trigger"""
        if self.position_side == PositionSide.LONG:
            return current_price >= self.take_profit_price
        else:  # SHORT
            return current_price <= self.take_profit_price


class ScalpingStrategyConfig(BaseModel):
    """Scalping strategy configuration"""
    symbol: Symbol
    position_size: float = 0.01  # Base position size per trade
    max_positions: int = 2  # Maximum concurrent positions (long + short)
    stop_loss_rate: float = 0.005  # 0.5% stop loss
    take_profit_rate: float = 0.01  # 1% take profit
    atr_multiple: float = 1.0  # AlphaTrend ATR multiplier
    period: int = 8  # AlphaTrend period
    signal_reverse: bool = False  # Reverse the signal direction
    enable_short_trades: bool = True  # Allow short positions
    enable_long_trades: bool = True  # Allow long positions
    backup_file_path: str = "data/scalping_strategy_state.json"  # Path to store strategy state
    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.NORMAL  # Place order behavior

class ScalpingStrategy(StrategyV2):
    def __init__(self, ex_client: ExSwapClient, config: ScalpingStrategyConfig):
        super().__init__()
        self.config = config
        self.ex_client = ex_client

        # Initialize signals
        self.long_signal = AlphaTrendSignal(OrderSide.BUY, self.config.atr_multiple, self.config.period, reverse=self.config.signal_reverse)
        self.short_signal = AlphaTrendSignal(OrderSide.SELL, self.config.atr_multiple, self.config.period, reverse=self.config.signal_reverse)

        # Position tracking
        self.positions: Dict[str, ScalpPosition] = {}  # order_id -> position
        self.active_long_positions: int = 0
        self.active_short_positions: int = 0

        # Performance tracking
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.total_pnl: float = 0.0

        # Thread safety
        self.lock = threading.Lock()

        # Load previous state if exists
        self._load_state()

        logger.info(f"ScalpingStrategy initialized for {config.symbol.binance()}")

    def _generate_order_id(self, side: OrderSide) -> str:
        """Generate unique order ID"""
        return f"{side.value}{secrets.token_hex(nbytes=5)}"

    def _calculate_position_size(self, current_price: float) -> float:
        """Calculate position size based on risk parameters"""
        # Simple position sizing - can be enhanced with more sophisticated risk management
        return self.config.position_size

    def _can_open_position(self, position_side: PositionSide) -> bool:
        """Check if we can open a new position"""
        total_positions = self.active_long_positions + self.active_short_positions
        if total_positions >= self.config.max_positions:
            return False

        if position_side == PositionSide.LONG and not self.config.enable_long_trades:
            return False

        if position_side == PositionSide.SHORT and not self.config.enable_short_trades:
            return False

        return True

    def _open_position(self, position_side: PositionSide, entry_price: float) -> Optional[str]:
        """Open a new position"""
        if not self._can_open_position(position_side):
            return None

        position_size = self._calculate_position_size(entry_price)

        # Calculate stop loss and take profit prices
        if position_side == PositionSide.LONG:
            stop_loss_price = entry_price * (1 - self.config.stop_loss_rate)
            take_profit_price = entry_price * (1 + self.config.take_profit_rate)
            order_side = OrderSide.BUY
        else:  # SHORT
            stop_loss_price = entry_price * (1 + self.config.stop_loss_rate)
            take_profit_price = entry_price * (1 - self.config.take_profit_rate)
            order_side = OrderSide.SELL

        # Generate order ID
        order_id = self._generate_order_id(order_side)

        try:
            # Place market order
            order_result = self.ex_client.place_order_v2(
                custom_id=order_id,
                symbol=self.config.symbol,
                order_side=order_side,
                quantity=position_size,
                price=entry_price,
                position_side=position_side,
                place_order_behavior=self.config.place_order_behavior
            )

            if order_result and order_result.get('clientOrderId'):
                # Create position tracking
                position = ScalpPosition(
                    position_side=position_side,
                    entry_price=entry_price,
                    quantity=position_size,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    entry_order_id=order_result['clientOrderId']
                )

                self.positions[order_result['clientOrderId']] = position

                if position_side == PositionSide.LONG:
                    self.active_long_positions += 1
                else:
                    self.active_short_positions += 1

                # Save state after opening position
                self._save_state()

                logger.info(f"Opened {position_side.value} position: {position_size} @ {entry_price} (ID: {order_result['clientOrderId']})")
                return order_result['clientOrderId']

        except Exception as e:
            logger.error(f"Failed to open {position_side.value} position: {e}")

        return None

    def _close_position(self, position: ScalpPosition, exit_price: float, reason: str) -> bool:
        """Close an existing position"""
        if not position.entry_order_id:
            return False

        # Determine exit order side
        if position.position_side == PositionSide.LONG:
            exit_order_side = OrderSide.SELL
        else:
            exit_order_side = OrderSide.BUY

        exit_order_id = self._generate_order_id(exit_order_side)

        try:
            # Place exit order
            order_result = self.ex_client.place_order_v2(
                custom_id=exit_order_id,
                symbol=self.config.symbol,
                order_side=exit_order_side,
                quantity=position.quantity,
                price=exit_price,
                position_side=position.position_side,
                place_order_behavior=self.config.place_order_behavior
            )

            if order_result and order_result.get('clientOrderId'):
                position.exit_order_id = order_result['clientOrderId']

                # Calculate realized P&L
                realized_pnl = position.update_pnl(exit_price)
                self.total_pnl += realized_pnl
                self.total_trades += 1

                if realized_pnl > 0:
                    self.winning_trades += 1

                # Update position counts
                if position.position_side == PositionSide.LONG:
                    self.active_long_positions -= 1
                else:
                    self.active_short_positions -= 1

                # Remove from tracking
                if position.entry_order_id in self.positions:
                    del self.positions[position.entry_order_id]

                # Save state after closing position
                self._save_state()

                logger.info(f"Closed {position.position_side.value} position: {realized_pnl:.4f} P&L ({reason})")
                return True

        except Exception as e:
            logger.error(f"Failed to close {position.position_side.value} position: {e}")

        return False

    def _check_signals_and_trade(self):
        """Check signals and execute trades"""
        df = self.klines_to_dataframe()
        if df.empty:
            return

        current_price = self.last_kline.close

        # Check long signals
        if self.long_signal.is_entry(df):
            if self._can_open_position(PositionSide.LONG):
                self._open_position(PositionSide.LONG, current_price)

        elif self.long_signal.is_exit(df):
            # Close long positions on exit signal
            positions_to_close = [
                pos for pos in self.positions.values()
                if pos.position_side == PositionSide.LONG
            ]
            for position in positions_to_close:
                self._close_position(position, current_price, "signal_exit")

        # Check short signals
        if self.short_signal.is_entry(df):
            if self._can_open_position(PositionSide.SHORT):
                self._open_position(PositionSide.SHORT, current_price)

        elif self.short_signal.is_exit(df):
            # Close short positions on exit signal
            positions_to_close = [
                pos for pos in self.positions.values()
                if pos.position_side == PositionSide.SHORT
            ]
            for position in positions_to_close:
                self._close_position(position, current_price, "signal_exit")

    def _manage_positions(self):
        """Manage existing positions (stop loss, take profit)"""
        current_price = self.last_kline.close
        positions_to_close: List[Tuple[ScalpPosition, float, str]] = []

        for position in self.positions.values():
            position.update_pnl(current_price)

            # Check stop loss
            if position.should_stop_loss(current_price):
                positions_to_close.append((position, current_price, "stop_loss"))
                continue

            # Check take profit
            if position.should_take_profit(current_price):
                positions_to_close.append((position, current_price, "take_profit"))
                continue

        # Close positions that hit risk targets
        for position, exit_price, reason in positions_to_close:
            self._close_position(position, exit_price, reason)

    def on_kline_finished(self):
        """Main strategy logic - called when K-line is finished"""
        if not self.lock.acquire(blocking=False):
            return

        try:
            # Check signals and execute new trades
            self._check_signals_and_trade()

            # Manage existing positions
            self._manage_positions()

        except Exception as e:
            logger.error(f"Error in scalping strategy execution: {e}")
        finally:
            self.lock.release()

    def _get_backup_file_path(self) -> str:
        """Get the backup file path for this strategy instance"""
        return self.config.backup_file_path

    def _save_state(self):
        """Save current strategy state to file"""
        try:
            state: dict[str, Any] = {
                'positions': self.positions,
                'active_long_positions': self.active_long_positions,
                'active_short_positions': self.active_short_positions,
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'total_pnl': self.total_pnl
            }

            backup_path = self._get_backup_file_path()
            if not os.path.exists(backup_path):
                logger.info(f"Backup file {backup_path} does not exist, creating a new one.")
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

            # Restore state
            self.positions = {}
            for order_id, pos_data in state_data.get('positions', {}).items():
                # Reconstruct ScalpPosition from dict
                position = ScalpPosition(**pos_data)
                self.positions[order_id] = position

            self.active_long_positions = state_data.get('active_long_positions', 0)
            self.active_short_positions = state_data.get('active_short_positions', 0)
            self.total_trades = state_data.get('total_trades', 0)
            self.winning_trades = state_data.get('winning_trades', 0)
            self.total_pnl = state_data.get('total_pnl', 0.0)

            logger.info(f"Strategy state loaded from {backup_path}: {len(self.positions)} positions, "
                       f"{self.total_trades} trades, PNL: {self.total_pnl:.4f}")

        except Exception as e:
            logger.error(f"Failed to load strategy state from {backup_path}: {e}")
            # Continue with fresh state if loading fails

    def get_performance_stats(self) -> Dict[str, float]:
        """Get current performance statistics"""
        win_rate = self.winning_trades / self.total_trades if self.total_trades > 0 else 0

        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'active_positions': len(self.positions),
            'active_longs': self.active_long_positions,
            'active_shorts': self.active_short_positions
        }
