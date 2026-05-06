import pytest
from strategy.signal_registry import SignalRegistry, register_signal
from model import OrderSide


class FakeSignal:
    def __init__(self, side: OrderSide):
        self.side = side


class FakeGridsSignal:
    def __init__(self, inner: FakeSignal):
        self.inner = inner
        self.side = inner.side


@pytest.fixture(autouse=True)
def clean_registry():
    SignalRegistry.clear()
    yield
    SignalRegistry.clear()


class TestSignalRegistry:
    def test_register_and_get(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        assert SignalRegistry.get("fake") is FakeSignal

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="not registered"):
            SignalRegistry.get("nonexistent")

    def test_list_types(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        SignalRegistry.register("fake_grids", FakeGridsSignal)
        types = SignalRegistry.list_types()
        assert "fake" in types
        assert "fake_grids" in types

    def test_from_config_simple(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        signal = SignalRegistry.from_config({"type": "fake", "side": OrderSide.BUY})
        assert isinstance(signal, FakeSignal)
        assert signal.side == OrderSide.BUY

    def test_from_config_coerces_side_string(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        signal = SignalRegistry.from_config({"type": "fake", "side": "BUY"})
        assert isinstance(signal, FakeSignal)
        assert signal.side == OrderSide.BUY

    def test_from_config_nested(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        SignalRegistry.register("fake_grids", FakeGridsSignal)
        signal = SignalRegistry.from_config({
            "type": "fake_grids",
            "inner": {"type": "fake", "side": OrderSide.SELL},
        })
        assert isinstance(signal, FakeGridsSignal)
        assert isinstance(signal.inner, FakeSignal)
        assert signal.inner.side == OrderSide.SELL

    def test_from_config_coerces_nested_side_string(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        SignalRegistry.register("fake_grids", FakeGridsSignal)
        signal = SignalRegistry.from_config({
            "type": "fake_grids",
            "inner": {"type": "fake", "side": "SELL"},
        })
        assert isinstance(signal, FakeGridsSignal)
        assert signal.inner.side == OrderSide.SELL

    def test_from_config_does_not_mutate_input(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        cfg = {"type": "fake", "side": OrderSide.BUY}
        original = {**cfg}
        SignalRegistry.from_config(cfg)
        assert cfg == original

    def test_decorator_registers(self) -> None:
        @register_signal("decorated")
        class DecoratedSignal:
            pass

        assert SignalRegistry.get("decorated") is DecoratedSignal

    def test_overwrite_warning(self) -> None:
        SignalRegistry.register("dup", FakeSignal)
        SignalRegistry.register("dup", FakeGridsSignal)
        assert SignalRegistry.get("dup") is FakeGridsSignal

    def test_clear(self) -> None:
        SignalRegistry.register("fake", FakeSignal)
        SignalRegistry.clear()
        assert SignalRegistry.list_types() == []

    def test_from_config_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError, match="not registered"):
            SignalRegistry.from_config({"type": "nonexistent"})
