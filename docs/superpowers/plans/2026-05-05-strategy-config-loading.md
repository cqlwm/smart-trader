# Strategy Config Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded strategy imports in BotManager with YAML-driven strategy loading, where strategies self-register with their Config classes and StrategyLoader builds KlineHandler instances from config.

**Architecture:** SignalRegistry + enhanced StrategyRegistry + StrategyLoader form the config-to-instance pipeline. Strategy classes register `(strategy_cls, config_cls)` pairs. StrategyLoader reads YAML, resolves special fields (symbol, signal, enums), builds Config instances, constructs strategies and wraps them in KlineHandler. BotManager delegates to StrategyLoader. Template directory is deleted.

**Tech Stack:** Python 3.11, PyYAML, Pydantic (existing), pytest (existing)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `strategy/signal_registry.py` | Signal type registry + `from_config` recursive builder |
| Modify | `strategy/registry.py` | Enhanced to store `(strategy_cls, config_cls)` pairs |
| Create | `strategy/loader.py` | YAML reader + config builder + KlineHandler factory |
| Create | `strategy/strategies/__init__.py` | Auto-discovery: imports all strategy modules |
| Modify | `strategy/alpha_trend_signal/alpha_trend_signal.py` | Add `@register_signal("alpha_trend")` |
| Modify | `strategy/alpha_trend_signal/alpha_trend_grids_signal.py` | Add `@register_signal("alpha_trend_grids")` |
| Modify | `strategy/signal_grid_strategy.py` | Add `@register_strategy("signal_grid", SignalGridStrategyConfig)`, remove `order_file_path` |
| Modify | `strategy/daily_trend_strategy.py` | Add `@register_strategy("daily_trend", DailyTrendStrategyConfig)`, remove `order_file_path` |
| Modify | `strategy/smc_signal/smc_intraday_strategy.py` | Add `@register_strategy("smc_intraday", SimpleIntradayConfig)`, adjust constructor |
| Modify | `strategy/simple_grid_strategy.py` | Add `@register_strategy("simple_grid", SimpleGridStrategyConfig)`, remove `backup_file` |
| Modify | `bot_manager.py` | Replace hardcoded imports with StrategyLoader |
| Modify | `run.py` | Pass config_path to BotManager |
| Create | `strategies.yaml` | Default strategy configuration |
| Delete | `template/` | Entire directory removed |
| Create | `test/test_signal_registry.py` | SignalRegistry unit tests |
| Create | `test/test_strategy_loader.py` | StrategyLoader unit + integration tests |
| Modify | `test/test_strategy_registry.py` | Update for new `(cls, config_cls)` registry API |

---

### Task 1: SignalRegistry

**Files:**
- Create: `strategy/signal_registry.py`
- Create: `test/test_signal_registry.py`

- [ ] **Step 1: Write failing tests for SignalRegistry**

```python
# test/test_signal_registry.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/test_signal_registry.py -v`
Expected: FAIL — `strategy.signal_registry` module not found

- [ ] **Step 3: Implement SignalRegistry**

```python
# strategy/signal_registry.py
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class SignalRegistry:
    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, signal_class: type) -> None:
        if name in cls._registry:
            logger.warning("Signal type '%s' already registered, overwriting", name)
        cls._registry[name] = signal_class
        logger.info("Registered signal type: %s -> %s", name, signal_class.__name__)

    @classmethod
    def get(cls, name: str) -> type:
        if name not in cls._registry:
            raise KeyError(
                f"Signal type '{name}' not registered. Available: {list(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def list_types(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()

    @classmethod
    def from_config(cls, config: dict) -> object:
        cfg = {**config}
        signal_type = cfg.pop("type")
        signal_cls = cls.get(signal_type)
        if "inner" in cfg:
            cfg["inner"] = cls.from_config(cfg.pop("inner"))
        return signal_cls(**cfg)


def register_signal(name: str) -> Callable[[type], type]:
    def decorator(signal_class: type) -> type:
        SignalRegistry.register(name, signal_class)
        return signal_class
    return decorator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/test_signal_registry.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/signal_registry.py test/test_signal_registry.py
git commit -m "feat: add SignalRegistry for config-driven signal construction"
```

---

### Task 2: Enhance StrategyRegistry

**Files:**
- Modify: `strategy/registry.py`
- Modify: `test/test_strategy_registry.py`

- [ ] **Step 1: Update StrategyRegistry to store `(strategy_cls, config_cls)` pairs**

```python
# strategy/registry.py — full replacement
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class StrategyRegistry:
    _registry: dict[str, tuple[type, type]] = {}

    @classmethod
    def register(cls, name: str, strategy_class: type, config_class: type) -> None:
        if name in cls._registry:
            logger.warning("Strategy type '%s' already registered, overwriting", name)
        cls._registry[name] = (strategy_class, config_class)
        logger.info("Registered strategy type: %s -> (%s, %s)", name, strategy_class.__name__, config_class.__name__)

    @classmethod
    def get(cls, name: str) -> tuple[type, type]:
        if name not in cls._registry:
            raise KeyError(
                f"Strategy type '{name}' not registered. Available: {list(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def list_types(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()


def register_strategy(name: str, config_class: type) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        StrategyRegistry.register(name, cls, config_class)
        return cls
    return decorator
```

- [ ] **Step 2: Update existing tests for new registry API**

The existing tests in `test/test_strategy_registry.py` use the old `StrategyRegistry.register("name", Class)` and `register_strategy("name")` signatures. Update them:

```python
# test/test_strategy_registry.py — full replacement
import pytest

from strategy.registry import StrategyRegistry, register_strategy
from strategy.instance import InstanceStatus, StrategyInstance
from strategy.instance_manager import StrategyInstanceManager


class DummyStrategy:
    pass


class AnotherStrategy:
    pass


class DummyConfig:
    pass


class AnotherConfig:
    pass


@pytest.fixture(autouse=True)
def clean_registry():
    StrategyRegistry.clear()
    yield
    StrategyRegistry.clear()


class TestStrategyRegistry:
    def test_register_and_get(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy, DummyConfig)
        strategy_cls, config_cls = StrategyRegistry.get("dummy")
        assert strategy_cls is DummyStrategy
        assert config_cls is DummyConfig

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="not registered"):
            StrategyRegistry.get("nonexistent")

    def test_list_types(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy, DummyConfig)
        StrategyRegistry.register("another", AnotherStrategy, AnotherConfig)
        types = StrategyRegistry.list_types()
        assert "dummy" in types
        assert "another" in types
        assert len(types) == 2

    def test_decorator_registers(self) -> None:
        @register_strategy("decorated", DummyConfig)
        class DecoratedStrategy:
            pass

        strategy_cls, config_cls = StrategyRegistry.get("decorated")
        assert strategy_cls is DecoratedStrategy
        assert config_cls is DummyConfig

    def test_overwrite_warning(self) -> None:
        StrategyRegistry.register("dup", DummyStrategy, DummyConfig)
        StrategyRegistry.register("dup", AnotherStrategy, AnotherConfig)
        strategy_cls, config_cls = StrategyRegistry.get("dup")
        assert strategy_cls is AnotherStrategy
        assert config_cls is AnotherConfig

    def test_clear(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy, DummyConfig)
        StrategyRegistry.clear()
        assert StrategyRegistry.list_types() == []


class TestStrategyInstanceManager:
    def _setup_manager(self) -> StrategyInstanceManager:
        StrategyRegistry.register("dummy", DummyStrategy, DummyConfig)
        return StrategyInstanceManager()

    def test_create_instance(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {"param": 42})

        assert instance.strategy_type == "dummy"
        assert instance.config == {"param": 42}
        assert instance.status == InstanceStatus.PENDING
        assert instance.instance_id is not None
        assert instance.error_message is None

    def test_create_unknown_type_raises(self) -> None:
        mgr = StrategyInstanceManager()
        with pytest.raises(KeyError, match="not registered"):
            mgr.create("nonexistent", {})

    def test_start_instance(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        started = mgr.start(instance.instance_id)

        assert started.status == InstanceStatus.RUNNING
        assert started.error_message is None

    def test_start_already_running_is_noop(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.start(instance.instance_id)
        started_again = mgr.start(instance.instance_id)

        assert started_again.status == InstanceStatus.RUNNING

    def test_stop_instance(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.start(instance.instance_id)
        stopped = mgr.stop(instance.instance_id)

        assert stopped.status == InstanceStatus.STOPPED

    def test_stop_non_running_raises(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        with pytest.raises(ValueError, match="Cannot stop"):
            mgr.stop(instance.instance_id)

    def test_remove_stopped_instance(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.start(instance.instance_id)
        mgr.stop(instance.instance_id)
        mgr.remove(instance.instance_id)

        assert mgr.get(instance.instance_id) is None

    def test_remove_running_instance_raises(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.start(instance.instance_id)
        with pytest.raises(ValueError, match="Stop it first"):
            mgr.remove(instance.instance_id)

    def test_remove_pending_instance(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.remove(instance.instance_id)
        assert mgr.get(instance.instance_id) is None

    def test_get_nonexistent_returns_none(self) -> None:
        mgr = self._setup_manager()
        assert mgr.get("nonexistent") is None

    def test_list_all(self) -> None:
        mgr = self._setup_manager()
        mgr.create("dummy", {"a": 1})
        mgr.create("dummy", {"b": 2})

        instances = mgr.list_all()
        assert len(instances) == 2

    def test_restart_after_stop(self) -> None:
        mgr = self._setup_manager()
        instance = mgr.create("dummy", {})
        mgr.start(instance.instance_id)
        mgr.stop(instance.instance_id)
        restarted = mgr.start(instance.instance_id)

        assert restarted.status == InstanceStatus.RUNNING

    def test_two_instances_isolated(self) -> None:
        mgr = self._setup_manager()
        i1 = mgr.create("dummy", {"id": 1})
        i2 = mgr.create("dummy", {"id": 2})

        mgr.start(i1.instance_id)
        mgr.start(i2.instance_id)
        mgr.stop(i1.instance_id)

        assert mgr.get(i1.instance_id).status == InstanceStatus.STOPPED
        assert mgr.get(i2.instance_id).status == InstanceStatus.RUNNING
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/test_strategy_registry.py -v`
Expected: All tests PASS

- [ ] **Step 4: Fix all existing `@register_strategy` calls across the codebase**

The decorator signature changed from `@register_strategy("name")` to `@register_strategy("name", ConfigClass)`. The following files need updating (just the decorator line, no logic changes):

1. `strategy/signal_grid_strategy.py:248` — change `@register_strategy("signal_grid")` to `@register_strategy("signal_grid", SignalGridStrategyConfig)`
2. `strategy/daily_trend_strategy.py:29` — change `@register_strategy("daily_trend")` to `@register_strategy("daily_trend", DailyTrendStrategyConfig)`
3. `strategy/smc_signal/smc_intraday_strategy.py:23` — change `@register_strategy("smc_intraday")` to `@register_strategy("smc_intraday", SimpleIntradayConfig)`
4. `strategy/simple_grid_strategy.py:228` — change `@register_strategy("simple_grid")` to `@register_strategy("simple_grid", SimpleGridStrategyConfig)`

For each file, only the decorator line changes. Example for signal_grid_strategy.py:

```python
@register_strategy("signal_grid", SignalGridStrategyConfig)
class SignalGridStrategy(SimpleStrategy):
```

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add strategy/registry.py test/test_strategy_registry.py strategy/signal_grid_strategy.py strategy/daily_trend_strategy.py strategy/smc_signal/smc_intraday_strategy.py strategy/simple_grid_strategy.py
git commit -m "feat: enhance StrategyRegistry to store (strategy_cls, config_cls) pairs"
```

---

### Task 3: Register Signals

**Files:**
- Modify: `strategy/alpha_trend_signal/alpha_trend_signal.py`
- Modify: `strategy/alpha_trend_signal/alpha_trend_grids_signal.py`

- [ ] **Step 1: Add `@register_signal` to AlphaTrendSignal**

In `strategy/alpha_trend_signal/alpha_trend_signal.py`, add the import and decorator:

Add at top:
```python
from strategy.signal_registry import register_signal
```

Change class definition from:
```python
class AlphaTrendSignal(Signal):
```
to:
```python
@register_signal("alpha_trend")
class AlphaTrendSignal(Signal):
```

- [ ] **Step 2: Add `@register_signal` to AlphaTrendGridsSignal**

In `strategy/alpha_trend_signal/alpha_trend_grids_signal.py`, add the import and decorator:

Add at top:
```python
from strategy.signal_registry import register_signal
```

Change class definition from:
```python
class AlphaTrendGridsSignal(Signal):
```
to:
```python
@register_signal("alpha_trend_grids")
class AlphaTrendGridsSignal(Signal):
```

- [ ] **Step 3: Verify no import errors**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal; from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal; from strategy.signal_registry import SignalRegistry; assert 'alpha_trend' in SignalRegistry.list_types(); assert 'alpha_trend_grids' in SignalRegistry.list_types(); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add strategy/alpha_trend_signal/alpha_trend_signal.py strategy/alpha_trend_signal/alpha_trend_grids_signal.py
git commit -m "feat: register AlphaTrendSignal and AlphaTrendGridsSignal in SignalRegistry"
```

---

### Task 4: Remove `order_file_path` from Config classes

**Files:**
- Modify: `strategy/signal_grid_strategy.py`
- Modify: `strategy/daily_trend_strategy.py`

- [ ] **Step 1: Remove `order_file_path` from SignalGridStrategyConfig**

In `strategy/signal_grid_strategy.py`, remove line 231:
```python
    order_file_path: str = 'data/grids_strategy_v2.json'
```

Also remove the `field_serializer` for `order_file_path` if one exists — there is a `field_serializer("signal")` at line 233-237, keep that.

- [ ] **Step 2: Remove `order_file_path` from DailyTrendStrategyConfig**

In `strategy/daily_trend_strategy.py`, remove the `order_file_path` field from `DailyTrendStrategyConfig` (line 25):
```python
    order_file_path: str
```

- [ ] **Step 3: Remove `backup_file` from SimpleGridStrategyConfig**

In `strategy/simple_grid_strategy.py`, remove line 225:
```python
    backup_file: str = ""
```

Also in `SimpleGridStrategy.__init__`, remove the backup_file logic (lines ~247-250):
```python
        if self.config.backup_file:
            self.backup_file = self.config.backup_file
        else:
            self.backup_file = f"{DATA_PATH}/backup_{self.config.symbol.simple()}_{self.config.position_side.value}_{self.config.master_order_side.value}.json"
```

And change `self.load_state()` call — it no longer needs the backup_file attribute.

- [ ] **Step 4: Verify no import or construction errors**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "from strategy.signal_grid_strategy import SignalGridStrategyConfig; from strategy.daily_trend_strategy import DailyTrendStrategyConfig; from strategy.simple_grid_strategy import SimpleGridStrategyConfig; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add strategy/signal_grid_strategy.py strategy/daily_trend_strategy.py strategy/simple_grid_strategy.py
git commit -m "refactor: remove order_file_path and backup_file from strategy configs"
```

---

### Task 5: StrategyLoader

**Files:**
- Create: `strategy/loader.py`
- Create: `test/test_strategy_loader.py`

- [ ] **Step 1: Write failing tests for StrategyLoader**

```python
# test/test_strategy_loader.py
import pytest
import tempfile
import os
from unittest.mock import MagicMock

from strategy.loader import StrategyLoader
from strategy.registry import StrategyRegistry
from strategy.signal_registry import SignalRegistry
from model import OrderSide, PositionSide, Symbol


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
        yaml_path = _write_yaml("""
strategies:
  - type: fake
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
""")
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
        yaml_path = _write_yaml("""
strategies:
  - type: fake
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
  - type: fake
    config:
      symbol: {base: ETH, quote: USDC}
      timeframe: 15m
""")
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 2
        finally:
            os.unlink(yaml_path)

    def test_skip_unknown_strategy_type(self) -> None:
        _register_fake()
        yaml_path = _write_yaml("""
strategies:
  - type: nonexistent
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
  - type: fake
    config:
      symbol: {base: ETH, quote: USDC}
      timeframe: 15m
""")
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert len(handlers) == 1
        finally:
            os.unlink(yaml_path)

    def test_skip_config_error(self) -> None:
        StrategyRegistry.register("fake", FakeStrategy, FakeConfig)
        yaml_path = _write_yaml("""
strategies:
  - type: fake
    config:
      timeframe: 5m
""")
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
        yaml_path = _write_yaml("""
strategies:
  - type: fake
    config:
      symbol: {base: doge, quote: usdc}
      timeframe: 5m
""")
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            assert handlers[0].strategy.symbol == Symbol(base="DOGE", quote="USDC")
        finally:
            os.unlink(yaml_path)

    def test_enum_parsed_from_string(self) -> None:
        _register_fake()
        yaml_path = _write_yaml("""
strategies:
  - type: fake
    config:
      symbol: {base: BTC, quote: USDC}
      timeframe: 5m
      position_side: LONG
""")
        try:
            loader = StrategyLoader(yaml_path)
            handlers = loader.load(MagicMock())
            # FakeConfig accepts kwargs, so check it was passed through
            assert handlers[0].strategy.config.symbol == Symbol(base="BTC", quote="USDC")
        finally:
            os.unlink(yaml_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/test_strategy_loader.py -v`
Expected: FAIL — `strategy.loader` module not found

- [ ] **Step 3: Implement StrategyLoader**

```python
# strategy/loader.py
import logging
from pathlib import Path

import yaml

from event_loop.handler.kline_handler import KlineHandler
from model import OrderSide, PositionSide, Symbol
from strategy.registry import StrategyRegistry
from strategy.signal_registry import SignalRegistry

logger = logging.getLogger(__name__)

_ENUM_MAP: dict[str, type] = {
    "position_side": PositionSide,
    "master_side": OrderSide,
    "side": OrderSide,
    "master_order_side": OrderSide,
    "entry_side": OrderSide,
}


class StrategyLoader:
    def __init__(self, config_path: str) -> None:
        self._config_path = config_path

    def load(self, ex_client: object) -> list[KlineHandler]:
        raw = self._read_yaml()
        handlers: list[KlineHandler] = []
        for entry in raw.get("strategies", []):
            handler = self._load_one(entry, ex_client)
            if handler is not None:
                handlers.append(handler)
        return handlers

    def _load_one(self, entry: dict, ex_client: object) -> KlineHandler | None:
        strategy_type = entry.get("type")
        if strategy_type is None:
            logger.error("Strategy entry missing 'type' field, skipping")
            return None

        try:
            strategy_cls, config_cls = StrategyRegistry.get(strategy_type)
        except KeyError as e:
            logger.error("Strategy type '%s' not registered. Available: %s. Skipping.",
                         strategy_type, StrategyRegistry.list_types())
            return None

        try:
            raw_config = entry.get("config", {})
            config = self._build_config(config_cls, raw_config)
            strategy = strategy_cls(config, ex_client)
            return KlineHandler(strategy)
        except Exception as e:
            logger.error("Failed to build strategy '%s': %s", strategy_type, e)
            return None

    def _build_config(self, config_cls: type, raw: dict) -> object:
        resolved = self._resolve_fields(raw)
        return config_cls(**resolved)

    def _resolve_fields(self, raw: dict) -> dict:
        resolved: dict = {}
        for key, value in raw.items():
            if key in _ENUM_MAP and isinstance(value, str):
                resolved[key] = _ENUM_MAP[key](value.lower())
            elif key == "symbol" and isinstance(value, dict):
                resolved[key] = Symbol(base=value["base"], quote=value["quote"])
            elif key == "signal" and isinstance(value, dict):
                resolved[key] = SignalRegistry.from_config(value)
            else:
                resolved[key] = value
        return resolved

    def _read_yaml(self) -> dict:
        path = Path(self._config_path)
        if not path.exists():
            logger.critical("Strategy config file not found: %s", self._config_path)
            raise FileNotFoundError(f"Strategy config file not found: {self._config_path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/test_strategy_loader.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/loader.py test/test_strategy_loader.py
git commit -m "feat: add StrategyLoader for YAML-driven strategy construction"
```

---

### Task 6: Strategy Auto-Discovery Module

**Files:**
- Create: `strategy/strategies/__init__.py`

- [ ] **Step 1: Create auto-discovery module**

```python
# strategy/strategies/__init__.py
from strategy.signal_grid_strategy import SignalGridStrategy  # noqa: F401
from strategy.daily_trend_strategy import DailyTrendStrategy  # noqa: F401
from strategy.smc_signal.smc_intraday_strategy import SMCIntradayStrategy  # noqa: F401
from strategy.simple_grid_strategy import SimpleGridStrategy  # noqa: F401

from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal  # noqa: F401
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal  # noqa: F401
```

- [ ] **Step 2: Verify registration works**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "import strategy.strategies; from strategy.registry import StrategyRegistry; from strategy.signal_registry import SignalRegistry; print('Strategies:', StrategyRegistry.list_types()); print('Signals:', SignalRegistry.list_types())"`
Expected: Both lists contain the registered types

- [ ] **Step 3: Commit**

```bash
git add strategy/strategies/__init__.py
git commit -m "feat: add strategy auto-discovery module"
```

---

### Task 7: Simplify BotManager

**Files:**
- Modify: `bot_manager.py`
- Modify: `run.py`

- [ ] **Step 1: Rewrite BotManager to use StrategyLoader**

```python
# bot_manager.py — full replacement
import threading
import logging

from client.ex_client import ExSwapClient
from event_loop.base import DataEventLoop
from event_loop.binance import BinanceDataEventLoop
from event_loop.handler.kline_handler import KlineHandler
from strategy.instance_manager import StrategyInstanceManager
from strategy.loader import StrategyLoader
import strategy.strategies  # noqa: F401 — trigger auto-registration
import dotenv

dotenv.load_dotenv()
logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self, ex_client: ExSwapClient, el: DataEventLoop, config_path: str = "strategies.yaml") -> None:
        self.ex_client: ExSwapClient = ex_client
        self.data_event_loop: DataEventLoop = el
        self._config_path = config_path
        self._thread: threading.Thread | None = None
        self.instance_manager = StrategyInstanceManager()

    def start_bot(self) -> None:
        loader = StrategyLoader(self._config_path)
        handlers: list[KlineHandler] = loader.load(self.ex_client)

        kline_subscribes: list[str] = []
        self.data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

        for handler in handlers:
            for symbol in handler.strategy.symbols:
                for timeframe in handler.strategy.timeframes:
                    k = symbol.binance_ws_sub_kline(timeframe)
                    if k not in kline_subscribes:
                        kline_subscribes.append(k)

            self.data_event_loop.add_handler(handler)

        if len(kline_subscribes) == 0:
            logger.warning('No kline subscribes found')
            return

        logger.info("Starting BinanceDataEventLoop...")
        self.data_event_loop.start()

    def start_in_background(self) -> None:
        logger.info("Starting bot in background thread...")
        thread = threading.Thread(target=self.start_bot, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        logger.info("Stopping BotManager...")
        if self.data_event_loop:
            self.data_event_loop.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BotManager stopped.")
```

- [ ] **Step 2: Update run.py to pass config_path**

In `run.py`, the `--no-api` branch needs to pass `config_path`:

```python
def main():
    if "--no-api" in sys.argv:
        config_path = "strategies.yaml"
        for arg in sys.argv:
            if arg.startswith("--config="):
                config_path = arg.split("=", 1)[1]
        BotManager(
            ex_client=create_binance_client("MAIN"),
            el=BinanceDataEventLoop(kline_subscribes=[]),
            config_path=config_path,
        ).start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
```

- [ ] **Step 3: Verify no import errors**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "from bot_manager import BotManager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add bot_manager.py run.py
git commit -m "feat: simplify BotManager to use StrategyLoader"
```

---

### Task 8: Create strategies.yaml

**Files:**
- Create: `strategies.yaml`

- [ ] **Step 1: Create the default strategies config**

```yaml
strategies:
  - type: signal_grid
    config:
      symbol: {base: DOGE, quote: USDC}
      timeframe: 5m
      position_side: LONG
      master_side: BUY
      per_order_qty: 3500
      grid_spacing_rate: -0.1
      max_order: 3
      enable_exit_signal: true
      signal:
        type: alpha_trend_grids
        inner:
          type: alpha_trend
          side: BUY
      exit_signal_take_profit_min_rate: 0.03
      fixed_rate_take_profit: true
      fixed_take_profit_rate: 0.03
      enable_max_order_stop_loss: true
      position_reverse: false

  - type: signal_grid
    config:
      symbol: {base: DOGE, quote: USDC}
      timeframe: 5m
      position_side: SHORT
      master_side: SELL
      per_order_qty: 200
      grid_spacing_rate: 0.0001
      max_order: 10
      enable_exit_signal: true
      signal:
        type: alpha_trend_grids
        inner:
          type: alpha_trend
          side: BUY
      exit_signal_take_profit_min_rate: 0.005
      fixed_rate_take_profit: true
      fixed_take_profit_rate: 0.01
      enable_max_order_stop_loss: true
      position_reverse: true

  - type: smc_intraday
    config:
      symbol: {base: BTC, quote: USDC}
      timeframes: [1w, 1d, 5m]
      risk_per_trade_pct: 1.0
      account_balance: 100.0
```

- [ ] **Step 2: Verify YAML is valid and loads correctly**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "import yaml; data = yaml.safe_load(open('strategies.yaml')); print(f'Loaded {len(data[\"strategies\"])} strategies')"`
Expected: `Loaded 3 strategies`

- [ ] **Step 3: Commit**

```bash
git add strategies.yaml
git commit -m "feat: add default strategies.yaml config"
```

---

### Task 9: Adapt SMCIntradayStrategy for config-driven construction

**Files:**
- Modify: `strategy/smc_signal/smc_intraday_strategy.py`

The current `SMCIntradayStrategy.__init__` takes `(symbols, timeframes, ex_client, config: dict)` but the config-driven flow passes `(config: SimpleIntradayConfig, ex_client)`. The strategy needs to extract `symbols` and `timeframes` from the config.

- [ ] **Step 1: Rewrite SMCIntradayStrategy constructor**

Change the `__init__` to accept `(config: SimpleIntradayConfig, ex_client)`:

```python
# strategy/smc_signal/smc_intraday_strategy.py — updated __init__
@register_strategy("smc_intraday", SimpleIntradayConfig)
class SMCIntradayStrategy(GeneralStrategy):

    def __init__(
        self,
        config: SimpleIntradayConfig,
        ex_client: ExSwapClient,
    ):
        symbol = Symbol(
            base=config.symbol.split("/")[0],
            quote=config.symbol.split("/")[1].split(":")[0],
        )
        super().__init__(symbols=[symbol], timeframes=list(config.timeframes))
        self.ex_client = ex_client
        self._strategy_config = config
        self._smc_strategy = SimpleIntradayStrategy(self._strategy_config)
        self.strategy_id: str = f"smc_intraday_{symbol.simple()}"
        self.order_repo = ex_client.order_repo
        self._last_action: str = "WAIT"
```

Also remove the old `config: dict` parameter from `__init__` and the `SimpleIntradayConfig(**config)` line.

Also, the YAML config for smc_intraday needs `symbol` as a string (e.g., `"BTC/USDC"`) not a dict, because `SimpleIntradayConfig.symbol` is a `str`. Update `strategies.yaml` smc_intraday entry:

```yaml
  - type: smc_intraday
    config:
      symbol: BTC/USDC
      timeframes: [1w, 1d, 5m]
      risk_per_trade_pct: 1.0
      account_balance: 100.0
```

Update the StrategyLoader's `_resolve_fields` to handle the case where `symbol` is already a string (don't try to parse it as dict):

In `strategy/loader.py`, change the symbol handling:

```python
            elif key == "symbol" and isinstance(value, dict):
                resolved[key] = Symbol(base=value["base"], quote=value["quote"])
```

This already handles it correctly — if `symbol` is a string in YAML, it falls through to the `else` branch and passes through as-is.

- [ ] **Step 2: Verify SMCIntradayStrategy loads from StrategyLoader**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "
import strategy.strategies
from strategy.loader import StrategyLoader
from strategy.registry import StrategyRegistry
from strategy.signal_registry import SignalRegistry
print('Strategies:', StrategyRegistry.list_types())
print('Signals:', SignalRegistry.list_types())
loader = StrategyLoader('strategies.yaml')
from unittest.mock import MagicMock
mock = MagicMock()
mock.order_repo = MagicMock()
handlers = loader.load(mock)
for h in handlers:
    print(f'  {h.strategy.__class__.__name__}: symbols={[s.ccxt() for s in h.strategy.symbols]}, timeframes={h.strategy.timeframes}')
print(f'Total: {len(handlers)} handlers')
"`
Expected: 3 handlers loaded, each with correct symbols and timeframes

- [ ] **Step 3: Commit**

```bash
git add strategy/smc_signal/smc_intraday_strategy.py strategies.yaml strategy/loader.py
git commit -m "refactor: adapt SMCIntradayStrategy for config-driven construction"
```

---

### Task 10: Delete template directory

**Files:**
- Delete: `template/`

- [ ] **Step 1: Verify no other code imports from template**

Run: `cd /Users/li/projects/qt/smart-trader && grep -r "from template" --include="*.py" . | grep -v ".venv" | grep -v "__pycache__"`
Expected: No results (BotManager was already updated in Task 7)

- [ ] **Step 2: Delete the template directory**

```bash
rm -rf template/
```

- [ ] **Step 3: Verify no import errors**

Run: `cd /Users/li/projects/qt/smart-trader && uv run python -c "from bot_manager import BotManager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add -A template/
git commit -m "chore: delete template directory (replaced by config-driven loading)"
```

---

### Task 11: Full Integration Test

**Files:**
- Modify: `test/test_strategy_loader.py`

- [ ] **Step 1: Add integration test that loads from the actual strategies.yaml**

```python
# Add to test/test_strategy_loader.py

class TestStrategyLoaderIntegration:
    def test_load_actual_strategies_yaml(self) -> None:
        """Integration: load strategies.yaml and verify all handlers constructed."""
        import strategy.strategies  # noqa: F401 — trigger registration

        yaml_path = os.path.join(os.path.dirname(__file__), "..", "strategies.yaml")
        if not os.path.exists(yaml_path):
            pytest.skip("strategies.yaml not found")

        loader = StrategyLoader(yaml_path)
        mock_client = MagicMock()
        mock_client.order_repo = MagicMock()
        handlers = loader.load(mock_client)
        assert len(handlers) == 3

        strategy_types = [h.strategy.__class__.__name__ for h in handlers]
        assert "SignalGridStrategy" in strategy_types
        assert "SMCIntradayStrategy" in strategy_types
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add test/test_strategy_loader.py
git commit -m "test: add integration test for strategies.yaml loading"
```

---

### Task 12: Final Verification

- [ ] **Step 1: Run mypy type check**

Run: `cd /Users/li/projects/qt/smart-trader && uv run mypy strategy/loader.py strategy/signal_registry.py strategy/registry.py bot_manager.py --ignore-missing-imports`
Expected: No errors

- [ ] **Step 2: Run full test suite one last time**

Run: `cd /Users/li/projects/qt/smart-trader && uv run pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address type check and test issues"
```
