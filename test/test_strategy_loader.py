import os
import tempfile

import pytest
from unittest.mock import MagicMock

from model import OrderSide, PositionSide, Symbol
from strategy.loader import StrategyLoader
from strategy.registry import StrategyRegistry
from strategy.signal_registry import SignalRegistry


@pytest.fixture(autouse=True)
def clean_registries():
    StrategyRegistry.clear()
    SignalRegistry.clear()
    yield
    StrategyRegistry.clear()
    SignalRegistry.clear()


class FakeConfig:
    def __init__(self, symbol: Symbol, timeframe: str, **kwargs):
        self.symbol = symbol
        self.timeframe = timeframe
        self.symbols = [symbol]
        self.timeframes = [timeframe]


class FakeStrategy:
    def __init__(self, config: FakeConfig, ex_client):
        self.config = config
        self.symbols = config.symbols
        self.timeframes = config.timeframes

    @property
    def symbol(self):
        return self.symbols[0]


class FakeSignal:
    def __init__(self, side: OrderSide):
        self.side = side


def _register_fake():
    StrategyRegistry.register("fake", FakeStrategy, FakeConfig)
    SignalRegistry.register("fake_signal", FakeSignal)


def _write_yaml(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


class TestStrategyLoader:
    def test_load_single_strategy(self) -> None:
        _register_fake()
        yaml_path = _write_yaml(
            """
strategies:
  - type: fake
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
"""
        )
        try:
            loader = StrategyLoader(yaml_path)
            mock_client = MagicMock()
            handlers = loader.load(mock_client)
            assert len(handlers) == 1
            assert handlers[0].strategy.symbols[0] == Symbol(base="BTC", quote="USDC")
            assert handlers[0].strategy.timeframes == ["5m"]
        finally:
            os.unlink(yaml_path)

    def test_load_multiple_strategies(self) -> None:
        _register_fake()
        yaml_path = _write_yaml(
            """
strategies:
  - type: fake
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
  - type: fake
    config:
      symbol: {base: ETH, quote: USDC}
      timeframe: 15m
"""
        )
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 2
        finally:
            os.unlink(yaml_path)

    def test_skip_unknown_strategy_type(self) -> None:
        _register_fake()
        yaml_path = _write_yaml(
            """
strategies:
  - type: nonexistent
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
  - type: fake
    config:
      symbol: {base: ETH, quote: USDC}
      timeframe: 15m
"""
        )
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 1
        finally:
            os.unlink(yaml_path)

    def test_skip_config_error(self) -> None:
        StrategyRegistry.register("fake", FakeStrategy, FakeConfig)
        yaml_path = _write_yaml(
            """
strategies:
  - type: fake
    config:
      timeframe: 5m
"""
        )
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 0
        finally:
            os.unlink(yaml_path)

    def test_empty_strategies_list(self) -> None:
        _register_fake()
        yaml_path = _write_yaml("strategies: []")
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 0
        finally:
            os.unlink(yaml_path)

    def test_missing_yaml_raises(self) -> None:
        loader = StrategyLoader("/nonexistent/path.yaml")
        with pytest.raises(FileNotFoundError):
            loader.load(MagicMock())

    def test_symbol_parsed_from_dict(self) -> None:
        _register_fake()
        yaml_path = _write_yaml(
            """
strategies:
  - type: fake
    config:
      symbol: {base: doge, quote: usdc}
      timeframe: 5m
"""
        )
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert handlers[0].strategy.symbol == Symbol(base="DOGE", quote="USDC")
        finally:
            os.unlink(yaml_path)
