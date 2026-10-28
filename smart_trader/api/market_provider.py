"""行情数据源的惰性提供者

`create_public_market_data()` 会触网加载市场; API 启动时不构造, 首次请求才初始化,
失败也不影响控制台其它页面。成功一次后缓存复用。
"""

import threading
from collections.abc import Callable

from smart_trader.exchange.ports import MarketDataPort


class MarketDataProvider:
    """线程安全的惰性行情实例"""

    def __init__(self, factory: Callable[[], MarketDataPort]):
        self._factory = factory
        self._market: MarketDataPort | None = None
        self._lock = threading.Lock()

    def get(self) -> MarketDataPort:
        market = self._market
        if market is not None:
            return market
        with self._lock:
            if self._market is None:
                self._market = self._factory()
            return self._market
