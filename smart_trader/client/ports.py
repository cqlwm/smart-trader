"""交易所端口

零实现、零第三方依赖: 业务层只依赖这里的 Protocol, 适配器负责实现。
"""

from typing import Protocol

from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderResult,
    Position,
    Symbol,
    SymbolInfo,
)


class MarketDataPort(Protocol):
    """只读行情与交易规则"""

    def symbol_info(self, symbol: Symbol) -> SymbolInfo: ...

    def latest_price(self, symbol: Symbol) -> float: ...

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]: ...


class TradingPort(Protocol):
    """下单与订单查询; 失败时抛出 SmartTraderError"""

    def place(self, request: OrderRequest) -> OrderResult: ...

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        """查询订单; 订单不存在时返回 None"""
        ...

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        """撤单; 订单不存在时返回 None"""
        ...


class AccountPort(Protocol):
    """账户与持仓"""

    def balance(self, coin: str) -> float: ...

    def positions(self, symbol: Symbol | None = None) -> list[Position]: ...
