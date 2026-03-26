import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from strategy.instance import InstanceStatus, StrategyInstance
from strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class StrategyInstanceManager:
    """Manages strategy instance lifecycle: create, start, stop, remove."""

    def __init__(self) -> None:
        self._instances: dict[str, StrategyInstance] = {}
        self._live_strategies: dict[str, object] = {}

    def create(self, strategy_type: str,
               config: dict[str, str | int | float | bool | list | dict]) -> StrategyInstance:
        StrategyRegistry.get(strategy_type)

        instance_id = uuid.uuid4().hex[:12]
        instance = StrategyInstance(
            instance_id=instance_id,
            strategy_type=strategy_type,
            config=config,
            status=InstanceStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._instances[instance_id] = instance
        logger.info("Created strategy instance %s (type=%s)", instance_id, strategy_type)
        return instance

    def start(self, instance_id: str) -> StrategyInstance:
        instance = self._get_or_raise(instance_id)
        if instance.status == InstanceStatus.RUNNING:
            return instance

        if instance.status not in (InstanceStatus.PENDING, InstanceStatus.STOPPED, InstanceStatus.ERROR):
            raise ValueError(f"Cannot start instance in status {instance.status.value}")

        try:
            strategy_cls = StrategyRegistry.get(instance.strategy_type)
            logger.info("Starting strategy instance %s (type=%s)", instance_id, instance.strategy_type)
            self._live_strategies[instance_id] = strategy_cls
            updated = replace(instance, status=InstanceStatus.RUNNING, error_message=None)
        except Exception as e:
            logger.error("Failed to start instance %s: %s", instance_id, e)
            updated = replace(instance, status=InstanceStatus.ERROR, error_message=str(e))

        self._instances[instance_id] = updated
        return updated

    def stop(self, instance_id: str) -> StrategyInstance:
        instance = self._get_or_raise(instance_id)
        if instance.status == InstanceStatus.STOPPED:
            return instance

        if instance.status != InstanceStatus.RUNNING:
            raise ValueError(f"Cannot stop instance in status {instance.status.value}")

        self._live_strategies.pop(instance_id, None)
        updated = replace(instance, status=InstanceStatus.STOPPED)
        self._instances[instance_id] = updated
        logger.info("Stopped strategy instance %s", instance_id)
        return updated

    def remove(self, instance_id: str) -> None:
        instance = self._get_or_raise(instance_id)
        if instance.status == InstanceStatus.RUNNING:
            raise ValueError("Cannot remove a running instance. Stop it first.")

        self._live_strategies.pop(instance_id, None)
        del self._instances[instance_id]
        logger.info("Removed strategy instance %s", instance_id)

    def get(self, instance_id: str) -> StrategyInstance | None:
        return self._instances.get(instance_id)

    def list_all(self) -> list[StrategyInstance]:
        return list(self._instances.values())

    def _get_or_raise(self, instance_id: str) -> StrategyInstance:
        instance = self._instances.get(instance_id)
        if instance is None:
            raise KeyError(f"Instance '{instance_id}' not found")
        return instance
