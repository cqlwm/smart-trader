import pytest
from strategies.smc.models.types import (
    Bias, EventType, OBStatus, OrderBlock, Pivot, StructureEvent, StructureState,
)
from strategies.smc.models.signal_types import Signal, SignalState, SignalStatus
from strategies.smc.signal import (
    calculate_stop_loss,
    compute_signals,
    find_entry_ob,
    find_take_profit_ob,
)


def _make_pivot(bar_index: int = 0, price: float = 100.0, is_high: bool = True) -> Pivot:
    return Pivot(
        price=price,
        bar_time=f"t{bar_index:03d}",
        label="HH" if is_high else "LL",
        is_high=is_high,
    )


def _make_event(
    bar_index: int = 5,
    bias: Bias = Bias.BULLISH,
    event_type: EventType = EventType.CHOCH,
    price: float = 105.0,
) -> StructureEvent:
    return StructureEvent(
        event_type=event_type,
        bias=bias,
        price=price,
        time=f"t{bar_index:03d}",
        pivot=_make_pivot(bar_index, price, is_high=(bias == Bias.BULLISH)),
    )


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


class TestFindEntryOb:
    def test_returns_most_recent_matching_ob(self) -> None:
        ob1 = _make_ob(bias=Bias.BULLISH, formed_index=2)
        ob2 = _make_ob(bias=Bias.BULLISH, formed_index=5)
        event = _make_event(bias=Bias.BULLISH)
        result = find_entry_ob([ob1, ob2], event)
        assert result is not None
        assert result.formed_time == "t005"

    def test_returns_none_when_no_matching_obs(self) -> None:
        ob = _make_ob(bias=Bias.BEARISH)
        event = _make_event(bias=Bias.BULLISH)
        assert find_entry_ob([ob], event) is None

    def test_skips_mitigated_obs(self) -> None:
        ob1 = _make_ob(bias=Bias.BULLISH, formed_index=5, status=OBStatus.MITIGATED)
        ob2 = _make_ob(bias=Bias.BULLISH, formed_index=3)
        event = _make_event(bias=Bias.BULLISH)
        result = find_entry_ob([ob1, ob2], event)
        assert result is not None
        assert result.formed_time == "t003"

    def test_empty_list_returns_none(self) -> None:
        event = _make_event(bias=Bias.BULLISH)
        assert find_entry_ob([], event) is None


class TestFindTakeProfitOb:
    def test_returns_opposite_ob_satisfying_rr(self) -> None:
        tp_ob = _make_ob(bias=Bias.BEARISH, low=120.0, high=125.0, formed_index=2)
        entry = 100.0
        stop_loss = 95.0
        result = find_take_profit_ob([tp_ob], Bias.BULLISH, entry, stop_loss, min_rr=2.0)
        assert result is not None
        assert result.bias == Bias.BEARISH

    def test_returns_none_when_rr_too_low(self) -> None:
        tp_ob = _make_ob(bias=Bias.BEARISH, low=102.0, high=105.0, formed_index=2)
        entry = 100.0
        stop_loss = 95.0
        result = find_take_profit_ob([tp_ob], Bias.BULLISH, entry, stop_loss, min_rr=2.0)
        assert result is None

    def test_short_direction_uses_ob_high(self) -> None:
        tp_ob = _make_ob(bias=Bias.BULLISH, low=75.0, high=80.0, formed_index=2)
        entry = 100.0
        stop_loss = 105.0
        result = find_take_profit_ob([tp_ob], Bias.BEARISH, entry, stop_loss, min_rr=2.0)
        assert result is not None
        rr = abs(80.0 - entry) / abs(entry - stop_loss)
        assert rr > 2.0

    def test_no_opposite_direction_returns_none(self) -> None:
        ob = _make_ob(bias=Bias.BULLISH, low=75.0, high=80.0, formed_index=2)
        result = find_take_profit_ob([ob], Bias.BULLISH, 100.0, 95.0, min_rr=2.0)
        assert result is None


class TestCalculateStopLoss:
    def test_long_stop_loss(self) -> None:
        ob = _make_ob(bias=Bias.BULLISH, low=98.0)
        result = calculate_stop_loss(ob, Bias.BULLISH, atr=10.0, atr_multiplier=0.5)
        assert result == 98.0 - 10.0 * 0.5

    def test_short_stop_loss(self) -> None:
        ob = _make_ob(bias=Bias.BEARISH, high=102.0)
        result = calculate_stop_loss(ob, Bias.BEARISH, atr=10.0, atr_multiplier=0.5)
        assert result == 102.0 + 10.0 * 0.5


class TestComputeSignals:
    def _make_smc_result(
        self,
        swing_event: StructureEvent | None = None,
        internal_event: StructureEvent | None = None,
        swing_obs: list[OrderBlock] | None = None,
        internal_obs: list[OrderBlock] | None = None,
        current_atr: float = 10.0,
    ) -> object:
        from unittest.mock import MagicMock
        from strategies.smc.models.types import FairValueGap, EqualLevel, TrailingExtremes, PremiumDiscount, ZonePosition
        import pandas as pd

        swing_state = StructureState(
            trend=Bias.BULLISH,
            last_event=swing_event,
            pivot_high=None,
            pivot_low=None,
            swing_labels=[],
        )
        internal_state = StructureState(
            trend=Bias.BULLISH,
            last_event=internal_event,
            pivot_high=None,
            pivot_low=None,
            swing_labels=[],
        )
        result = MagicMock()
        result.swing_state = swing_state
        result.internal_state = internal_state
        result.swing_order_blocks = swing_obs or []
        result.internal_order_blocks = internal_obs or []
        result.fvgs = []
        result.equal_levels = []
        result.trailing = TrailingExtremes(
            top=110.0, bottom=90.0, top_time="t001", bottom_time="t000", top_label="TH", bottom_label="BL",
        )
        result.premium_discount = PremiumDiscount(
            premium_zone_high=110.0, premium_zone_low=102.5,
            equilibrium=100.0,
            discount_zone_high=97.5, discount_zone_low=90.0,
            current_position=ZonePosition.EQUILIBRIUM,
        )
        result.df = pd.DataFrame({"high": [105.0], "low": [95.0], "close": [100.0]})
        result.atr_series = pd.Series([10.0])
        result.current_atr = current_atr
        result.volatility = "normal"
        return result

    def test_detects_new_swing_event_creates_signal(self) -> None:
        event = _make_event(bar_index=10, bias=Bias.BULLISH)
        ob = _make_ob(bias=Bias.BULLISH, formed_index=5)
        result = self._make_smc_result(swing_event=event, swing_obs=[ob])

        prev_state = SignalState(
            signals=[], last_swing_event_time="t005", last_internal_event_time="",
        )
        new_state = compute_signals(result, prev_state, "t010", 105.0, 95.0)

        assert len(new_state.signals) == 1
        assert new_state.signals[0].direction == Bias.BULLISH
        assert new_state.signals[0].status == SignalStatus.PENDING
        assert new_state.last_swing_event_time == "t010"

    def test_detects_new_internal_event_creates_signal(self) -> None:
        event = _make_event(bar_index=8, bias=Bias.BEARISH)
        ob = _make_ob(bias=Bias.BEARISH, formed_index=3)
        result = self._make_smc_result(internal_event=event, internal_obs=[ob])

        prev_state = SignalState(
            signals=[], last_swing_event_time="", last_internal_event_time="t005",
        )
        new_state = compute_signals(result, prev_state, "t008", 105.0, 95.0)

        assert len(new_state.signals) == 1
        assert new_state.signals[0].direction == Bias.BEARISH

    def test_no_new_events_no_new_signals(self) -> None:
        event = _make_event(bar_index=5, bias=Bias.BULLISH)
        result = self._make_smc_result(swing_event=event)

        prev_state = SignalState(
            signals=[], last_swing_event_time="t005", last_internal_event_time="",
        )
        new_state = compute_signals(result, prev_state, "t006", 105.0, 95.0)
        assert len(new_state.signals) == 0

    def test_cancels_signal_when_ob_mitigated(self) -> None:
        ob = _make_ob(bias=Bias.BULLISH, formed_index=3, status=OBStatus.MITIGATED)
        event = _make_event(bar_index=5, bias=Bias.BULLISH)
        existing_signal = Signal(
            id="sig1",
            ob=_make_ob(bias=Bias.BULLISH, formed_index=3, status=OBStatus.UNTESTED),
            event=event,
            direction=Bias.BULLISH,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=None,
            created_bar_time="t005",
            status=SignalStatus.PENDING,
        )
        prev_state = SignalState(
            signals=[existing_signal],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )

        result = self._make_smc_result(swing_event=event, swing_obs=[ob])
        new_state = compute_signals(result, prev_state, "t006", 105.0, 95.0)

        canceled = [s for s in new_state.signals if s.id == "sig1"]
        assert len(canceled) == 1
        assert canceled[0].status == SignalStatus.CANCELED

    def test_fills_signal_when_price_reaches_entry(self) -> None:
        event = _make_event(bar_index=5, bias=Bias.BULLISH)
        ob_untested = _make_ob(bias=Bias.BULLISH, formed_index=3, status=OBStatus.UNTESTED)
        existing_signal = Signal(
            id="sig1",
            ob=ob_untested,
            event=event,
            direction=Bias.BULLISH,
            entry_price=96.0,
            stop_loss=93.0,
            take_profit=None,
            created_bar_time="t005",
            status=SignalStatus.PENDING,
        )
        prev_state = SignalState(
            signals=[existing_signal],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )

        result = self._make_smc_result(swing_event=event, swing_obs=[ob_untested])
        new_state = compute_signals(result, prev_state, "t006", 105.0, 95.0)

        filled = [s for s in new_state.signals if s.id == "sig1"]
        assert len(filled) == 1
        assert filled[0].status == SignalStatus.FILLED

    def test_preserves_existing_signals(self) -> None:
        event = _make_event(bar_index=5, bias=Bias.BULLISH)
        ob_untested = _make_ob(bias=Bias.BULLISH, formed_index=3, status=OBStatus.UNTESTED)
        existing = Signal(
            id="sig1",
            ob=ob_untested,
            event=event,
            direction=Bias.BULLISH,
            entry_price=80.0,
            stop_loss=75.0,
            take_profit=None,
            created_bar_time="t005",
            status=SignalStatus.PENDING,
        )
        prev_state = SignalState(
            signals=[existing],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )

        result = self._make_smc_result(swing_event=event, swing_obs=[ob_untested])
        new_state = compute_signals(result, prev_state, "t006", 105.0, 95.0)

        assert any(s.id == "sig1" for s in new_state.signals)
        preserved = [s for s in new_state.signals if s.id == "sig1"][0]
        assert preserved.status == SignalStatus.PENDING

    def test_no_take_profit_when_rr_below_min(self) -> None:
        event = _make_event(bar_index=10, bias=Bias.BULLISH)
        ob = _make_ob(bias=Bias.BULLISH, formed_index=5)
        result = self._make_smc_result(
            swing_event=event, swing_obs=[ob], current_atr=100.0,
        )

        prev_state = SignalState(
            signals=[], last_swing_event_time="t005", last_internal_event_time="",
        )
        new_state = compute_signals(result, prev_state, "t010", 105.0, 95.0)

        assert len(new_state.signals) == 1
        assert new_state.signals[0].take_profit is None

    def test_short_signal_fills_on_high_reaching_entry(self) -> None:
        event = _make_event(bar_index=5, bias=Bias.BEARISH)
        ob_untested = _make_ob(bias=Bias.BEARISH, formed_index=3, status=OBStatus.UNTESTED)
        existing = Signal(
            id="sig1",
            ob=ob_untested,
            event=event,
            direction=Bias.BEARISH,
            entry_price=104.0,
            stop_loss=109.0,
            take_profit=None,
            created_bar_time="t005",
            status=SignalStatus.PENDING,
        )
        prev_state = SignalState(
            signals=[existing],
            last_swing_event_time="t005",
            last_internal_event_time="",
        )

        result = self._make_smc_result(swing_event=event, swing_obs=[ob_untested])
        new_state = compute_signals(result, prev_state, "t006", 105.0, 95.0)

        filled = [s for s in new_state.signals if s.id == "sig1"]
        assert len(filled) == 1
        assert filled[0].status == SignalStatus.FILLED
