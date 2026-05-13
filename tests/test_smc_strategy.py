from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from model import OrderSide, PositionSide, Symbol
from strategies.smc.models.signal_types import SMCStrategyConfig, TradingSignal, TradingSignalState, SignalStatus
from strategies.smc.models.types import Bias, OBStatus, OrderBlock, StructureEvent, EventType, Pivot
from strategies.smc.smc_strategy import SMCSignalStrategy


SYMBOL = Symbol(base="btc", quote="usdt")


def _make_ob(
    bias: Bias = Bias.BULLISH,
    high: float = 102.0,
    low: float = 98.0,
    formed_index: int = 3,
    status: OBStatus = OBStatus.UNTESTED,
) -> OrderBlock:
    return OrderBlock(
        id=f"ob_{bias.value}_{formed_index}",
        bias=bias,
        high=high,
        low=low,
        mid=(high + low) / 2,
        formed_time=f"t{formed_index:03d}",
        status=status,
        source="swing",
    )


def _make_event(bar_index: int = 5, bias: Bias = Bias.BULLISH) -> StructureEvent:
    return StructureEvent(
        event_type=EventType.CHOCH,
        bias=bias,
        price=105.0,
        time=f"t{bar_index:03d}",
        pivot=Pivot(price=105.0, bar_time=f"t{bar_index:03d}", label="HH", is_high=True),
    )


def _make_signal(
    signal_id: str = "sig1",
    direction: Bias = Bias.BULLISH,
    entry_price: float = 100.0,
    status: SignalStatus = SignalStatus.PENDING,
    ob: OrderBlock | None = None,
) -> TradingSignal:
    return TradingSignal(
        id=signal_id,
        ob=ob or _make_ob(bias=direction),
        event=_make_event(bias=direction),
        direction=direction,
        entry_price=entry_price,
        stop_loss=95.0,
        take_profit=None,
        created_bar_time="t005",
        status=status,
    )


class TestSMCSignalStrategyInit:
    def test_initial_state(self) -> None:
        config = SMCStrategyConfig(symbol="BTCUSDT", timeframe="15m")
        client = MagicMock()
        strategy = SMCSignalStrategy(config, client)
        assert strategy._signal_state.last_swing_event_time == ""
        assert strategy._signal_state.last_internal_event_time == ""
        assert len(strategy._signal_state.signals) == 0
        assert strategy.symbol == SYMBOL

    def test_symbol_parsing(self) -> None:
        config = SMCStrategyConfig(symbol="ETHUSDT", timeframe="1h")
        client = MagicMock()
        strategy = SMCSignalStrategy(config, client)
        assert strategy.symbol == Symbol(base="eth", quote="usdt")


class TestSMCSignalStrategyOnKline:
    def _make_strategy_with_df(self, df: pd.DataFrame | None = None) -> tuple[SMCSignalStrategy, MagicMock]:
        config = SMCStrategyConfig(symbol="BTCUSDT", timeframe="15m")
        client = MagicMock()
        strategy = SMCSignalStrategy(config, client)
        if df is None:
            df = pd.DataFrame({
                "high": [100.0] * 60,
                "low": [95.0] * 60,
                "close": [98.0] * 60,
                "open": [97.0] * 60,
                "volume": [1000.0] * 60,
                "datetime": pd.date_range("2024-01-01", periods=60, freq="15min"),
            })
        strategy.kline_data_dict[strategy.symbol]["15m"] = MagicMock(klines=df, latest_kline=None)
        return strategy, client

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.SMCEngine")
    def test_calls_compute_signals_on_kline_finished(self, mock_engine_cls, mock_compute) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_result = MagicMock()
        mock_engine.analyze.return_value = mock_result

        empty_state = TradingSignalState(signals=[], last_swing_event_time="", last_internal_event_time="")
        mock_compute.return_value = empty_state

        strategy, client = self._make_strategy_with_df()
        strategy._on_kline_finished()

        mock_engine.analyze.assert_called_once()
        mock_compute.assert_called_once()

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.SMCEngine")
    def test_places_order_for_new_pending_signal(self, mock_engine_cls, mock_compute) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.analyze.return_value = MagicMock()

        new_signal = _make_signal(direction=Bias.BULLISH, entry_price=100.0)
        new_state = TradingSignalState(
            signals=[new_signal],
            last_swing_event_time="t010",
            last_internal_event_time="",
        )
        mock_compute.return_value = new_state

        strategy, client = self._make_strategy_with_df()
        strategy._signal_state = TradingSignalState(signals=[], last_swing_event_time="", last_internal_event_time="")
        strategy._on_kline_finished()

        client.place_order_v2.assert_called_once()
        call_kwargs = client.place_order_v2.call_args
        assert call_kwargs.kwargs["order_side"] == OrderSide.BUY
        assert call_kwargs.kwargs["position_side"] == PositionSide.LONG
        assert call_kwargs.kwargs["price"] == 100.0

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.SMCEngine")
    def test_cancels_order_for_canceled_signal(self, mock_engine_cls, mock_compute) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.analyze.return_value = MagicMock()

        pending_signal = _make_signal(signal_id="sig1", status=SignalStatus.PENDING)
        prev_state = TradingSignalState(
            signals=[pending_signal],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )

        canceled_signal = _make_signal(signal_id="sig1", status=SignalStatus.CANCELED)
        new_state = TradingSignalState(
            signals=[canceled_signal],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )
        mock_compute.return_value = new_state

        strategy, client = self._make_strategy_with_df()
        strategy._signal_state = prev_state
        strategy._signal_orders = {"sig1": "smc_sig_sig1"}
        strategy._on_kline_finished()

        client.cancel.assert_called_once_with("smc_sig_sig1", strategy.symbol)

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.SMCEngine")
    def test_short_signal_places_sell_order(self, mock_engine_cls, mock_compute) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.analyze.return_value = MagicMock()

        new_signal = _make_signal(direction=Bias.BEARISH, entry_price=100.0)
        new_state = TradingSignalState(
            signals=[new_signal],
            last_swing_event_time="",
            last_internal_event_time="t010",
        )
        mock_compute.return_value = new_state

        strategy, client = self._make_strategy_with_df()
        strategy._signal_state = TradingSignalState(signals=[], last_swing_event_time="", last_internal_event_time="")
        strategy._on_kline_finished()

        call_kwargs = client.place_order_v2.call_args
        assert call_kwargs.kwargs["order_side"] == OrderSide.SELL
        assert call_kwargs.kwargs["position_side"] == PositionSide.SHORT
