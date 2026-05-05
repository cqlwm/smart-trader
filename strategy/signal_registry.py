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
