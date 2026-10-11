"""交易所端口

零实现、零第三方依赖: 业务层只依赖这里的端口, 适配器负责实现。
内部接口一律 abc.ABC + 显式继承（类型准则见 .memory/01-system-architecture.md 第二节）;
Protocol 仅用于第三方形状接缝（适配器内的 CcxtExchange）。
"""

from abc import ABC, abstractmethod

from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderResult,
    Position,
    Symbol,
    SymbolInfo,
)


class MarketDataPort(ABC):
    """只读行情与交易规则"""

    @abstractmethod
    def symbol_info(self, symbol: Symbol) -> SymbolInfo: ...

    @abstractmethod
    def latest_price(self, symbol: Symbol) -> float: ...

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]: ...


class TradingPort(ABC):
    """下单与订单查询; 失败时抛出 SmartTraderError"""

    @abstractmethod
    def place(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        """查询订单; 订单不存在时返回 None"""
        ...

    @abstractmethod
    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        """撤单; 订单不存在时返回 None"""
        ...


class AccountPort(ABC):
    """账户与持仓"""

    @abstractmethod
    def balance(self, coin: str) -> float: ...

    @abstractmethod
    def positions(self, symbol: Symbol | None = None) -> list[Position]: ...


class Exchange(MarketDataPort, TradingPort, AccountPort):
    """完整交易所能力 = 行情 + 交易 + 账户

    供装配层（bootstrap / template / demo）持有; 业务模块仍按需依赖单一端口。
    """
