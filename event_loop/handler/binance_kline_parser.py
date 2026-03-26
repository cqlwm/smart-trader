import json
import re
import logging
from typing import Any

from model import Symbol, Kline
from event_loop.event import KlineEvent

logger = logging.getLogger(__name__)


class BinanceKlineParser:
    """Parses Binance WebSocket JSON messages into KlineEvent objects."""

    def parse(self, raw_message: str) -> KlineEvent | None:
        data_obj: dict[str, Any] = json.loads(raw_message)

        kline_key: str = data_obj.get('stream', '')
        is_kline: bool = '@kline_' in kline_key
        kline_data: dict[str, Any] | None = data_obj.get('data', {}).get('k', None)

        if not is_kline or not kline_data:
            return None

        match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', kline_key)
        if not match:
            logger.warning("Invalid kline key: %s", kline_key)
            return None

        sym = Symbol(base=match.group(1), quote=match.group(2))
        kline = Kline(
            symbol=sym,
            timeframe=match.group(3),
            open=float(kline_data['o']),
            high=float(kline_data['h']),
            low=float(kline_data['l']),
            close=float(kline_data['c']),
            volume=float(kline_data['v']),
            timestamp=int(kline_data['t']),
            finished=kline_data.get('x', False),
        )

        return KlineEvent(timestamp=kline.timestamp, kline=kline)
