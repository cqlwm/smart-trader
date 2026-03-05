from types import SimpleNamespace

import pytest

from model import Symbol
from template.top10_short import build_top10_short_order_requests, fetch_top_gainers


class DummySymbolInfo:
    def format_qty(self, qty: float) -> float:
        return round(qty, 6)


class DummyExchangeClient:
    def __init__(self, tickers: dict[str, dict]):
        self.exchange = SimpleNamespace(
            fetch_tickers=lambda: tickers,
            set_sandbox_mode=lambda _: None,
        )

    def symbol_info(self, symbol: Symbol) -> DummySymbolInfo:
        return DummySymbolInfo()


def test_fetch_top_gainers_supports_losers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("template.top10_short.time.time", lambda: 2000.0)
    tickers = {
        "BTC/USDT": {"symbol": "BTC/USDT", "last": 100000, "percentage": 12.5, "timestamp": 2_000_000},
        "ETH/USDT": {"symbol": "ETH/USDT", "last": 3000, "percentage": -6.8, "timestamp": 2_000_000},
        "SOL/USDT": {"symbol": "SOL/USDT", "last": 200, "percentage": -3.1, "timestamp": 2_000_000},
        "XRP/USDT": {"symbol": "XRP/USDT", "last": 2.2, "percentage": 5.0, "timestamp": 2_000_000},
    }
    client = DummyExchangeClient(tickers=tickers)

    gainers = fetch_top_gainers(client, top_n=2, ranking_type="gainers")
    losers = fetch_top_gainers(client, top_n=2, ranking_type="losers")

    assert [item[0].binance() for item in gainers] == ["BTCUSDT", "XRPUSDT"]
    assert [item[0].binance() for item in losers] == ["ETHUSDT", "SOLUSDT"]


def test_build_requests_uses_ranking_type_losers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("template.top10_short.time.time", lambda: 3000.0)
    tickers = {
        "BTC/USDT": {"symbol": "BTC/USDT", "last": 100000, "percentage": 9.2, "timestamp": 3_000_000},
        "ETH/USDT": {"symbol": "ETH/USDT", "last": 2500, "percentage": -7.3, "timestamp": 3_000_000},
    }
    client = DummyExchangeClient(tickers=tickers)

    requests = build_top10_short_order_requests(
        exchange_client=client,
        per_symbol_usdt=100.0,
        top_n=1,
        ranking_type="losers",
    )

    assert len(requests) == 1
    assert requests[0].symbol.binance() == "ETHUSDT"
    assert requests[0].quantity == round(100.0 / 2500.0, 6)


def test_fetch_top_gainers_rejects_invalid_ranking_type() -> None:
    client = DummyExchangeClient(tickers={})

    with pytest.raises(ValueError, match="ranking_type"):
        fetch_top_gainers(client, ranking_type="invalid")
