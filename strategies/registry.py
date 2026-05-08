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
