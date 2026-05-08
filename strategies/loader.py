import logging
from pathlib import Path

import yaml

from event_loop.handler.kline_handler import KlineHandler
from model import OrderSide, PositionSide, Symbol
from strategies.registry import StrategyRegistry
from strategies.signal_registry import SignalRegistry

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
        except KeyError:
            logger.error(
                "Strategy type '%s' not registered. Available: %s. Skipping.",
                strategy_type,
                StrategyRegistry.list_types(),
            )
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
            raise FileNotFoundError(
                f"Strategy config file not found: {self._config_path}"
            )
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        return data
