from client.ex_client import ExClient
from strategies import SimpleStrategy
from strategies.registry import register_strategy
from strategies.smc.engine import SMCEngine
from strategies.smc.models.signal_types import SMCSignalConfig, SignalState, SignalStatus
from strategies.smc.signal import compute_signals
from model import OrderSide, PositionSide, Symbol
import log

logger = log.getLogger(__name__)


@register_strategy("smc_signal", SMCSignalConfig)
class SMCSignalStrategy(SimpleStrategy):

    def __init__(self, config: SMCSignalConfig, ex_client: ExClient) -> None:
        super().__init__(symbol=config.symbol, timeframe=config.timeframe)
        self.config = config
        self.ex_client = ex_client
        self._smc_engine = SMCEngine()
        self._signal_state = SignalState(
            signals=[],
            last_swing_event_time="",
            last_internal_event_time="",
        )
        self._signal_orders: dict[str, str] = {}

    def _on_kline_finished(self) -> None:
        df = self.klines_df
        if len(df) < 50:
            return

        result = self._smc_engine.analyze(df)

        current_bar_time = str(df["datetime"].iloc[-1])
        current_high = float(df["high"].iloc[-1])
        current_low = float(df["low"].iloc[-1])

        new_state = compute_signals(
            result=result,
            prev_state=self._signal_state,
            current_bar_time=current_bar_time,
            current_high=current_high,
            current_low=current_low,
        )

        self._handle_new_signals(new_state)
        self._handle_canceled_signals(new_state)

        self._signal_state = new_state

    def _handle_new_signals(self, new_state: SignalState) -> None:
        prev_ids = {s.id for s in self._signal_state.signals}
        for signal in new_state.signals:
            if signal.id not in prev_ids and signal.status == SignalStatus.PENDING:
                order_id = f"smc_sig_{signal.id}"
                side = OrderSide.BUY if signal.direction.value == 1 else OrderSide.SELL
                pos_side = PositionSide.LONG if signal.direction.value == 1 else PositionSide.SHORT
                try:
                    self.ex_client.place_order_v2(
                        strategy_id=self.strategy_id,
                        custom_id=order_id,
                        symbol=self.symbol,
                        order_side=side,
                        quantity=self.config.quantity,
                        price=signal.entry_price,
                        position_side=pos_side,
                    )
                    self._signal_orders[signal.id] = order_id
                    logger.info(
                        "Placed %s limit order at %.2f, SL=%.2f, TP=%s",
                        side.value, signal.entry_price, signal.stop_loss,
                        signal.take_profit,
                    )
                except Exception:
                    logger.exception("Failed to place order for signal %s", signal.id)

    def _handle_canceled_signals(self, new_state: SignalState) -> None:
        prev_pending = {
            s.id for s in self._signal_state.signals if s.status == SignalStatus.PENDING
        }
        for signal in new_state.signals:
            if signal.id in prev_pending and signal.status == SignalStatus.CANCELED:
                order_id = self._signal_orders.get(signal.id)
                if order_id:
                    try:
                        self.ex_client.cancel(order_id, self.symbol)
                        logger.info("Canceled order %s for signal %s", order_id, signal.id)
                    except Exception:
                        logger.exception("Failed to cancel order %s", order_id)
