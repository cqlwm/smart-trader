"""端口 ABC 契约: 抽象强制完整实现 + Exchange 组合接口

端口是内部接口, 实现类必须显式继承并完整实现（类型准则见
.memory/01-system-architecture.md 第二节）。本文件锁定 ABC 的强制语义,
防止端口退化回"结构化满足、无人校验"的形态。
"""

import pytest

from smart_trader.exchange.ports import (
    AccountPort,
    Exchange,
    MarketDataPort,
    TradingPort,
)
from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderResult,
    Position,
    Symbol,
    SymbolInfo,
)


class _FullExchange(Exchange):
    """完整实现三端口的形状类, 只用于验证 ABC 契约, 方法体不会被调用"""

    def symbol_info(self, symbol: Symbol) -> SymbolInfo:
        raise NotImplementedError

    def latest_price(self, symbol: Symbol) -> float:
        raise NotImplementedError

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        raise NotImplementedError

    def place(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        raise NotImplementedError

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        raise NotImplementedError

    def balance(self, coin: str) -> float:
        raise NotImplementedError

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        raise NotImplementedError


def test_exchange_composite_is_abstract() -> None:
    """Exchange 组合接口不可直接实例化

    实例化抽象类被 pyright 静态拦截, 此处用 ignore 做运行时语义验证:
    端口若退回 Protocol(实例化不再报错), 本测试变红。
    """
    with pytest.raises(TypeError, match="abstract"):
        Exchange()  # pyright: ignore[reportAbstractUsage]


def test_full_implementation_satisfies_exchange_and_all_ports() -> None:
    """完整实现三端口的类可实例化, 且同时是 Exchange 与三个端口的实例"""
    exchange = _FullExchange()

    assert isinstance(exchange, Exchange), "完整实现应满足 Exchange 组合接口"
    assert isinstance(exchange, MarketDataPort), "完整实现应满足 MarketDataPort"
    assert isinstance(exchange, TradingPort), "完整实现应满足 TradingPort"
    assert isinstance(exchange, AccountPort), "完整实现应满足 AccountPort"


def test_incomplete_market_data_rejected_at_instantiation() -> None:
    """MarketDataPort 实现缺方法时, 实例化立即报错而非调用时报错

    pyright 已静态拦截, ignore 用于锁定运行时 ABC 语义(Protocol 无此行为)。
    """

    class _MissingFetchOhlcv(MarketDataPort):
        def symbol_info(self, symbol: Symbol) -> SymbolInfo:
            raise NotImplementedError

        def latest_price(self, symbol: Symbol) -> float:
            raise NotImplementedError

    with pytest.raises(TypeError, match="abstract"):
        _MissingFetchOhlcv()  # pyright: ignore[reportAbstractUsage]


def test_incomplete_exchange_rejected_at_instantiation() -> None:
    """Exchange 子类缺任一端口方法时, 实例化立即报错"""

    class _MissingBalance(Exchange):
        def symbol_info(self, symbol: Symbol) -> SymbolInfo:
            raise NotImplementedError

        def latest_price(self, symbol: Symbol) -> float:
            raise NotImplementedError

        def fetch_ohlcv(
            self, symbol: Symbol, timeframe: str, limit: int = 100
        ) -> list[Kline]:
            raise NotImplementedError

        def place(self, request: OrderRequest) -> OrderResult:
            raise NotImplementedError

        def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
            raise NotImplementedError

        def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
            raise NotImplementedError

        def positions(self, symbol: Symbol | None = None) -> list[Position]:
            raise NotImplementedError

    with pytest.raises(TypeError, match="abstract"):
        _MissingBalance()  # pyright: ignore[reportAbstractUsage]
