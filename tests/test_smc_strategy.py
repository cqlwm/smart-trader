from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from model import OrderSide, PositionSide, Symbol
from strategies.smc.models.signal_types import SMCStrategyConfig, TradingSignal, TradingSignalState, SignalStatus
from strategies.smc.models.types import Bias, OBStatus, OrderBlock, StructureBreak, EventType, Pivot
from strategies.smc.smc_strategy import SMCSignalStrategy, _TradeInfo


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


def _make_event(bar_index: int = 5, bias: Bias = Bias.BULLISH) -> StructureBreak:
    return StructureBreak(
        event_type=EventType.CHOCH,
        bias=bias,
        price=105.0,
        time=f"t{bar_index:03d}",
        pivot=Pivot(price=105.0, bar_time=f"t{bar_index:03d}", label="HH", is_high=True),
        ob=None,
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
        config = SMCStrategyConfig(symbol=SYMBOL, timeframe="15m", quantity=1.0)
        client = MagicMock()
        strategy = SMCSignalStrategy(config, client)
        assert strategy._signal_state.last_swing_event_time == ""
        assert strategy._signal_state.last_internal_event_time == ""
        assert len(strategy._signal_state.signals) == 0
        assert strategy.symbol == SYMBOL

    def test_symbol_parsing(self) -> None:
        config = SMCStrategyConfig(symbol=Symbol(base="eth", quote="usdt"), timeframe="1h", quantity=1.0)
        client = MagicMock()
        strategy = SMCSignalStrategy(config, client)
        assert strategy.symbol == Symbol(base="eth", quote="usdt")


class TestSMCSignalStrategyOnKline:
    def _make_strategy_with_df(self, df: pd.DataFrame | None = None) -> tuple[SMCSignalStrategy, MagicMock]:
        config = SMCStrategyConfig(symbol=SYMBOL, timeframe="15m", quantity=1.0)
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
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_compute_signals_called_with_large_bias(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BULLISH
        mock_result_state = TradingSignalState(
            signals=[], last_swing_event_time="", last_internal_event_time="",
        )
        mock_compute.return_value = mock_result_state

        strategy, client = self._make_strategy_with_df()
        strategy._on_kline_finished()

        _, call_kwargs = mock_compute.call_args
        assert call_kwargs["large_bias"] == Bias.BULLISH

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_places_order_for_new_pending_signal(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BULLISH

        new_signal = _make_signal(direction=Bias.BULLISH, entry_price=100.0)
        new_state = TradingSignalState(
            signals=[new_signal],
            last_swing_event_time="t010",
            last_internal_event_time="",
        )
        mock_compute.return_value = new_state

        strategy, client = self._make_strategy_with_df()
        strategy._signal_state = TradingSignalState(
            signals=[], last_swing_event_time="", last_internal_event_time="",
        )
        strategy._on_kline_finished()

        client.place_order_v2.assert_called_once()
        call_kwargs = client.place_order_v2.call_args
        assert call_kwargs.kwargs["order_side"] == OrderSide.BUY
        assert call_kwargs.kwargs["position_side"] == PositionSide.LONG
        assert call_kwargs.kwargs["price"] == 100.0

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_cancels_order_for_canceled_signal(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BULLISH

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
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_short_signal_places_sell_order(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BEARISH

        new_signal = _make_signal(direction=Bias.BEARISH, entry_price=100.0)
        new_state = TradingSignalState(
            signals=[new_signal],
            last_swing_event_time="",
            last_internal_event_time="t010",
        )
        mock_compute.return_value = new_state

        strategy, client = self._make_strategy_with_df()
        strategy._signal_state = TradingSignalState(
            signals=[], last_swing_event_time="", last_internal_event_time="",
        )
        strategy._on_kline_finished()

        call_kwargs = client.place_order_v2.call_args
        assert call_kwargs.kwargs["order_side"] == OrderSide.SELL
        assert call_kwargs.kwargs["position_side"] == PositionSide.SHORT

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_closes_trade_against_large_bias(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BULLISH
        mock_compute.return_value = TradingSignalState(
            signals=[], last_swing_event_time="t010", last_internal_event_time="",
        )

        strategy, client = self._make_strategy_with_df()
        strategy._active_trades["trade1"] = _TradeInfo(
            order_id="ord1", position_side=PositionSide.SHORT,
            quantity=1.0, entry_price=100.0, stop_loss=110.0, take_profit=90.0,
        )
        strategy._on_kline_finished()

        assert "trade1" not in strategy._active_trades

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_keeps_trade_aligned_with_large_bias(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.BULLISH
        mock_compute.return_value = TradingSignalState(
            signals=[], last_swing_event_time="t010", last_internal_event_time="",
        )

        strategy, client = self._make_strategy_with_df()
        strategy._active_trades["trade1"] = _TradeInfo(
            order_id="ord1", position_side=PositionSide.LONG,
            quantity=1.0, entry_price=100.0, stop_loss=85.0, take_profit=None,
        )
        strategy._on_kline_finished()

        assert "trade1" in strategy._active_trades

    @patch("strategies.smc.smc_strategy.compute_signals")
    @patch("strategies.smc.smc_strategy.get_structure_bias")
    @patch("strategies.smc.smc_strategy.structure_info")
    def test_neutral_large_bias_keeps_all_trades(
        self, mock_si, mock_gsb, mock_compute
    ) -> None:
        mock_si.return_value = MagicMock()
        mock_gsb.return_value = Bias.NEUTRAL
        mock_compute.return_value = TradingSignalState(
            signals=[], last_swing_event_time="t010", last_internal_event_time="",
        )

        strategy, client = self._make_strategy_with_df()
        strategy._active_trades["trade1"] = _TradeInfo(
            order_id="ord1", position_side=PositionSide.SHORT,
            quantity=1.0, entry_price=100.0, stop_loss=110.0, take_profit=80.0,
        )
        strategy._on_kline_finished()

        assert "trade1" in strategy._active_trades
