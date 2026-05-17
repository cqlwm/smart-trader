import pandas as pd
from client.ex_client import ExClient
from strategies import SimpleStrategy
from strategies.registry import register_strategy
from strategies.smc.models.signal_types import SMCStrategyConfig, TradingSignalState, SignalStatus
from strategies.smc.models.types import Bias
from strategies.smc.signal import compute_signals
from dataclasses import dataclass
from strategies.smc.core.structure import structure_info, get_structure_bias
from strategies.smc.indicators.atr import compute_atr
from model import OrderSide, PositionSide, Symbol
import log

logger = log.getLogger(__name__)


@dataclass
class _TradeInfo:
    order_id: str
    position_side: PositionSide
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float | None


@register_strategy("smc", SMCStrategyConfig)
class SMCSignalStrategy(SimpleStrategy):

    def __init__(self, config: SMCStrategyConfig, ex_client: ExClient) -> None:
        super().__init__(symbol=config.symbol, timeframe=config.timeframe)
        self.config = config
        self.ex_client = ex_client
        self._signal_state = TradingSignalState(
            signals=[],
            last_swing_event_time="",
            last_internal_event_time="",
        )
        self._signal_orders: dict[str, str] = {}
        self._active_trades: dict[str, _TradeInfo] = {}

        self._large_bias = self.large_bias()

    def large_bias(self):
        large_klines = self.ex_client.fetch_ohlcv(self.config.symbol, '1d', limit=1000)
        large_timeframe_df = pd.DataFrame([k.to_dict() for k in large_klines])
        large_structure_info = structure_info(large_timeframe_df, self.config.swing_length)
        return get_structure_bias(large_structure_info)

    def _on_kline_finished(self) -> None:
        df = self.klines_df
        if len(df) < 50:
            return

        si = structure_info(df, self.config.swing_length)
        atr_value = float(compute_atr(df, self.config.atr_period).iloc[-1])

        current_bar_time = str(df["datetime"].iloc[-1])
        current_high = float(df["high"].iloc[-1])
        current_low = float(df["low"].iloc[-1])

        new_state = compute_signals(
            structure_info=si,
            prev_state=self._signal_state,
            current_bar_time=current_bar_time,
            current_high=current_high,
            current_low=current_low,
            atr=atr_value,
            large_bias=self._large_bias,
        )

        self._handle_new_signals(new_state)
        self._handle_canceled_signals(new_state)

        self._signal_state = new_state

        self._check_filled_orders(current_high, current_low)
        self._close_against_trend_trades(large_bias)

    def _close_against_trend_trades(self, large_bias: Bias) -> None:
        if large_bias == Bias.NEUTRAL:
            return
        for signal_id, trade in list(self._active_trades.items()):
            trade_bias = Bias.BULLISH if trade.position_side == PositionSide.LONG else Bias.BEARISH
            if trade_bias != large_bias:
                self._close_trade(trade)
                del self._active_trades[signal_id]
                logger.info(
                    "Closed %s trade %s (opposes large structure bias %s)",
                    trade.position_side.value, signal_id, large_bias.name,
                )

    def _handle_new_signals(self, new_state: TradingSignalState) -> None:
        prev_ids = {s.id for s in self._signal_state.signals}
        for signal in new_state.signals:
            if signal.id not in prev_ids and signal.status == SignalStatus.PENDING:
                order_id = f"smc_sig_{signal.id}"
                side = OrderSide.BUY if signal.direction == Bias.BULLISH else OrderSide.SELL
                pos_side = PositionSide.LONG if signal.direction == Bias.BULLISH else PositionSide.SHORT
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

    def _handle_canceled_signals(self, new_state: TradingSignalState) -> None:
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

    def _check_filled_orders(self, current_high: float, current_low: float) -> None:
        for signal_id, order_id in list(self._signal_orders.items()):
            order = self.ex_client.query_order(order_id, self.symbol)
            if order and order.status.value == "closed":
                signal = next(
                    (s for s in self._signal_state.signals if s.id == signal_id), None
                )
                if signal:
                    self._active_trades[signal_id] = _TradeInfo(
                        order_id=order_id,
                        position_side=PositionSide.LONG if signal.direction == Bias.BULLISH else PositionSide.SHORT,
                        quantity=self.config.quantity,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                    )
                    del self._signal_orders[signal_id]

        for signal_id, trade in list(self._active_trades.items()):
            should_close = False
            if trade.position_side == PositionSide.LONG:
                if current_low <= trade.stop_loss:
                    should_close = True
                elif trade.take_profit is not None and current_high >= trade.take_profit:
                    should_close = True
            else:
                if current_high >= trade.stop_loss:
                    should_close = True
                elif trade.take_profit is not None and current_low <= trade.take_profit:
                    should_close = True

            if should_close:
                self._close_trade(trade)
                del self._active_trades[signal_id]

    def _close_trade(self, trade: _TradeInfo) -> None:
        exit_side = OrderSide.SELL if trade.position_side == PositionSide.LONG else OrderSide.BUY
        exit_order_id = f"exit_{trade.order_id}"
        self.ex_client.place_order_v2(
            strategy_id=self.strategy_id,
            custom_id=exit_order_id,
            symbol=self.symbol,
            order_side=exit_side,
            quantity=trade.quantity,
            price=None,
            position_side=trade.position_side,
        )
        logger.info(
            "Closed %s trade at market: entry=%.4f",
            trade.position_side.value, trade.entry_price,
        )

    def get_chart_data(self) -> dict[str, list[dict]]:
        return {}
