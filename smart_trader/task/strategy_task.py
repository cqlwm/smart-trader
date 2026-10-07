import json
import re
from typing import Any

from smart_trader import log
from smart_trader.data_event_loop import Task
from smart_trader.model import Kline, Symbol
from smart_trader.strategy import MultiTimeframeStrategy

logger = log.getLogger(__name__)

_KLINE_STREAM_PATTERN = re.compile(r"(\w+?)(usdt|usdc|btc)@kline_(\w+)")
_KLINE_KEYS = ("t", "o", "h", "l", "c", "v")


def parse_kline_message(kline: dict[str, Any], stream: str) -> Kline | None:
    """
    解析币安 K 线推送消息
    @param kline: 消息体中的 data.k
    @param stream: 消息体中的 stream, 形如 dogeusdt@kline_1m
    @return: 无法解析时返回 None, 由调用方决定日志级别
    """
    match = _KLINE_STREAM_PATTERN.fullmatch(stream)
    if match is None:
        return None

    if any(key not in kline for key in _KLINE_KEYS):
        return None

    return Kline(
        # 币安推送流名全小写, 领域层统一使用大写
        symbol=Symbol(base=match.group(1).upper(), quote=match.group(2).upper()),
        timeframe=match.group(3),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
        timestamp=int(kline["t"]),
        finished=bool(kline.get("x", False)),
    )


class StrategyTask(Task):
    def __init__(self, symbol: Symbol, strategy: MultiTimeframeStrategy):
        super().__init__(
            f"StrategyTask({symbol.ccxt()}|{','.join(strategy.timeframes)})"
        )
        self.symbol: Symbol = symbol
        self.timeframes: list[str] = strategy.timeframes
        self.strategy: MultiTimeframeStrategy = strategy

    def run(self, data: str) -> None:
        data_obj: dict[str, Any] = json.loads(data)

        kline_key: str = data_obj.get("stream", "")
        kline: dict[str, Any] | None = data_obj.get("data", {}).get("k", None)

        if not kline:
            return

        kline_obj = parse_kline_message(kline, kline_key)
        if kline_obj is None:
            logger.debug("忽略无法解析的消息: stream=%s", kline_key)
            return

        if (
            self.symbol.binance() == kline_obj.symbol.binance()
            and kline_obj.timeframe in self.timeframes
        ):
            self.strategy.run(kline_obj)
