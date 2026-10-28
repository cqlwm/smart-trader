"""StrategyConfigPayload 契约: 默认值 / JSON 往返 / 信号物化 / 模板转换"""

import json

import pytest
from pandas import DataFrame

from smart_trader.model import (
    DataSource,
    MarketSignal,
    OrderSide,
    PlaceOrderBehavior,
    PositionSide,
    SignalKind,
    StrategySignalType,
    Symbol,
)
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.config_payload import StrategyConfigPayload
from smart_trader.strategy.signal import Signal
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig


def _template_config() -> SignalGridStrategyConfig:
    return SignalGridStrategyConfig(
        symbol=Symbol(base="doge", quote="usdt"),
        timeframe="1m",
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=500,
        grid_spacing_rate=0.0001,
        max_order=50,
        signal=AlphaTrendSignal(atr_multiple=1.5, period=10),
        signal_kind=SignalKind.TRIGGER,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.005,
        strategy_key="signal_grid_short_sell_dogeusdt_1m",
        enable_order_stop_loss=True,
        order_stop_loss_rate=0.05,
    )


class _OtherSignal(Signal):
    def evaluate(self, klines: DataFrame) -> MarketSignal:
        return MarketSignal()


def test_defaults_describe_a_single_timer_alpha_strategy():
    payload = StrategyConfigPayload()

    assert payload.symbol == "DOGE/USDT"
    assert payload.timeframe == "1m"
    assert payload.data_source is DataSource.TIMER
    assert payload.signal_type is StrategySignalType.ALPHA_TREND
    assert payload.position_side is PositionSide.LONG
    assert payload.master_side is OrderSide.BUY
    assert payload.per_order_qty == pytest.approx(0.02)
    assert payload.trend_spacing_rate is None
    assert payload.place_order_behavior is PlaceOrderBehavior.CHASER_OPEN


def test_json_roundtrip_preserves_every_field():
    payload = StrategyConfigPayload.from_strategy_config(
        _template_config(), data_source=DataSource.TIMER
    )

    restored = StrategyConfigPayload.from_json(payload.to_json())

    assert restored == payload, "JSON 往返必须无损"
    assert restored.signal_kind is SignalKind.TRIGGER
    assert restored.enable_order_stop_loss is True


def test_to_strategy_config_materializes_alpha_trend_signal():
    payload = StrategyConfigPayload(
        symbol="doge/usdt:usdt",
        timeframe="1m",
        data_source=DataSource.TIMER,
        signal_type=StrategySignalType.ALPHA_TREND,
        atr_multiple=1.7,
        period=12,
        position_side=PositionSide.SHORT,
        master_side=OrderSide.SELL,
        per_order_qty=123,
    )

    config = payload.to_strategy_config("grid_doge_1m")

    assert payload.symbol == "DOGE/USDT", "symbol 必须规范化为大写 BASE/QUOTE"
    assert config.strategy_key == "grid_doge_1m"
    assert config.symbol == Symbol(base="DOGE", quote="USDT")
    assert isinstance(config.signal, AlphaTrendSignal)
    assert config.signal.atr_multiple == pytest.approx(1.7)
    assert config.signal.period == 12
    assert config.position_side is PositionSide.SHORT
    assert config.master_side is OrderSide.SELL
    assert config.per_order_qty == pytest.approx(123)


def test_to_strategy_config_uses_none_signal_when_disabled():
    payload = StrategyConfigPayload(signal_type=StrategySignalType.NONE)

    config = payload.to_strategy_config("plain_grid")

    assert config.signal is None, "signal_type=none 必须物化为无信号策略"


def test_from_strategy_config_extracts_fields_and_signal_parameters():
    original = _template_config()

    payload = StrategyConfigPayload.from_strategy_config(
        original, data_source=DataSource.TIMER
    )

    assert payload.symbol == "DOGE/USDT"
    assert payload.timeframe == "1m"
    assert payload.data_source is DataSource.TIMER
    assert payload.signal_type is StrategySignalType.ALPHA_TREND
    assert payload.atr_multiple == pytest.approx(1.5)
    assert payload.period == 10
    assert payload.signal_kind is SignalKind.TRIGGER
    assert payload.position_side is PositionSide.SHORT
    assert payload.master_side is OrderSide.SELL
    assert payload.per_order_qty == pytest.approx(500)
    assert payload.enable_order_stop_loss is True
    assert payload.order_stop_loss_rate == pytest.approx(0.05)


def test_from_strategy_config_accepts_none_signal_as_signal_type_none():
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="doge", quote="usdt"),
        timeframe="1m",
        strategy_key="plain_grid",
        signal=None,
    )

    payload = StrategyConfigPayload.from_strategy_config(
        config, data_source=DataSource.WS
    )

    assert payload.signal_type is StrategySignalType.NONE
    assert payload.data_source is DataSource.WS


def test_from_strategy_config_rejects_unknown_signal_implementation():
    config = SignalGridStrategyConfig(
        symbol=Symbol(base="doge", quote="usdt"),
        timeframe="1m",
        strategy_key="custom_signal",
        signal=_OtherSignal(),
    )

    with pytest.raises(ValueError, match="不支持的信号实现"):
        StrategyConfigPayload.from_strategy_config(
            config, data_source=DataSource.WS
        )


@pytest.mark.parametrize("raw", ["", "   ", "DOGEUSDT", "DOGE/USDT/BTC"])
def test_invalid_symbol_is_rejected(raw: str):
    with pytest.raises(ValueError):
        StrategyConfigPayload(symbol=raw)


def test_empty_timeframe_is_rejected():
    with pytest.raises(ValueError):
        StrategyConfigPayload(timeframe="")


def test_unknown_field_is_rejected():
    data = json.loads(StrategyConfigPayload().to_json())
    data["unknown_field"] = 1

    with pytest.raises(ValueError):
        StrategyConfigPayload.model_validate(data)


def test_from_json_rejects_malformed_json():
    with pytest.raises(ValueError):
        StrategyConfigPayload.from_json("{not json")


def test_from_json_rejects_non_object_json():
    with pytest.raises(TypeError):
        StrategyConfigPayload.from_json("[]")


@pytest.mark.parametrize("field", ["period", "atr_multiple"])
def test_indicator_parameters_must_be_positive(field: str):
    data = json.loads(StrategyConfigPayload().to_json())
    data[field] = 0

    with pytest.raises(ValueError):
        StrategyConfigPayload.model_validate(data)
