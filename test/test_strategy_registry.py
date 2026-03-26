import pytest

from strategy.registry import StrategyRegistry, register_strategy
from strategy.instance import InstanceStatus, StrategyInstance
from strategy.instance_manager import StrategyInstanceManager


class DummyStrategy:
    pass


class AnotherStrategy:
    pass


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure each test starts with a clean registry."""
    StrategyRegistry.clear()
    yield
    StrategyRegistry.clear()


class TestStrategyRegistry:
    def test_register_and_get(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy)
        assert StrategyRegistry.get("dummy") is DummyStrategy

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="not registered"):
            StrategyRegistry.get("nonexistent")

    def test_list_types(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy)
        StrategyRegistry.register("another", AnotherStrategy)
        types = StrategyRegistry.list_types()
        assert "dummy" in types
        assert "another" in types
        assert len(types) == 2

    def test_decorator_registers(self) -> None:
        @register_strategy("decorated")
        class DecoratedStrategy:
            pass

        assert StrategyRegistry.get("decorated") is DecoratedStrategy

    def test_overwrite_warning(self) -> None:
        StrategyRegistry.register("dup", DummyStrategy)
        StrategyRegistry.register("dup", AnotherStrategy)
        assert StrategyRegistry.get("dup") is AnotherStrategy

    def test_clear(self) -> None:
        StrategyRegistry.register("dummy", DummyStrategy)
        StrategyRegistry.clear()
        assert StrategyRegistry.list_types() == []


class TestStrategyInstanceManager:
    def _setup_manager(self) -> StrategyInstanceManager:
        StrategyRegistry.register("dummy", DummyStrategy)
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
