from client.ex_client import ExClient
from strategies import SimpleStrategy
from strategies.registry import register_strategy
from strategies.smc.engine import SMCEngine
from strategies.smc.models.signal_types import SMCStrategyConfig, TradingSignalState, SignalStatus
from strategies.smc.signal import compute_signals
from dataclasses import dataclass

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
        self._smc_engine = SMCEngine(self.config.smc_config)
        self._signal_state = TradingSignalState(
            signals=[],
            last_swing_event_time="",
            last_internal_event_time="",
        )
        self._signal_orders: dict[str, str] = {}
        self._active_trades: dict[str, _TradeInfo] = {}

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

        self._check_filled_orders(current_high, current_low)

    def _handle_new_signals(self, new_state: TradingSignalState) -> None:
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
                        position_side=PositionSide.LONG if signal.direction.value == 1 else PositionSide.SHORT,
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
        """实现ChartDataProvider，返回SMC覆盖层数据"""
        df = self.klines_df
        if len(df) < 50:
            return {}

        result = self._smc_engine.analyze(df)

        from strategies.smc.core.structure import detect_structure_breaks
        from strategies.smc.core.legs import _detect_legs, identify_pivots

        structure_items = []

        swing_legs = _detect_legs(df["high"], df["low"], self._smc_engine.config.swing_length)
        swing_pivots_h, swing_pivots_l = identify_pivots(df, swing_legs, self._smc_engine.config.swing_length)
        swing_events, _ = detect_structure_breaks(df, swing_pivots_h, swing_pivots_l)

        for event in swing_events:
            structure_items.append({
                "type": event.event_type.name,
                "bias": event.bias.name,
                "price": event.price,
                "time": event.time,
                "source": "swing",
            })

        internal_legs = _detect_legs(df["high"], df["low"], self._smc_engine.config.internal_length)
        internal_pivots_h, internal_pivots_l = identify_pivots(df, internal_legs, self._smc_engine.config.internal_length)
        internal_events, _ = detect_structure_breaks(
            df, internal_pivots_h, internal_pivots_l,
            swing_pivots_high=swing_pivots_h,
            swing_pivots_low=swing_pivots_l,
            filter_confluence=self._smc_engine.config.internal_length != self._smc_engine.config.swing_length,
        )

        for event in internal_events:
            structure_items.append({
                "type": event.event_type.name,
                "bias": event.bias.name,
                "price": event.price,
                "time": event.time,
                "source": "internal",
            })

        ob_items = []
        for ob in result.swing_order_blocks:
            ob_items.append({
                "id": ob.id,
                "bias": ob.bias.name,
                "high": ob.high,
                "low": ob.low,
                "formed_time": ob.formed_time,
                "status": ob.status.name,
                "source": ob.source,
            })
        for ob in result.internal_order_blocks:
            ob_items.append({
                "id": ob.id,
                "bias": ob.bias.name,
                "high": ob.high,
                "low": ob.low,
                "formed_time": ob.formed_time,
                "status": ob.status.name,
                "source": ob.source,
            })

        fvg_items = []
        for fvg in result.fvgs:
            fvg_items.append({
                "id": fvg.id,
                "bias": fvg.bias.name,
                "top": fvg.top,
                "bottom": fvg.bottom,
                "formed_time": fvg.formed_time,
                "status": fvg.status.name,
            })

        data: dict[str, list[dict]] = {}
        if structure_items:
            data["structure"] = structure_items
        if ob_items:
            data["order_block"] = ob_items
        if fvg_items:
            data["fvg"] = fvg_items

        return data
