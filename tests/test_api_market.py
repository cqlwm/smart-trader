"""行情 API 测试: 正常图表 / 无数据源 / 数据源故障 / 参数校验"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.api.market_provider import MarketDataProvider
from smart_trader.exchange.ports import MarketDataPort
from smart_trader.model import Kline
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from tests.fakes import DEFAULT_SYMBOL, FakeMarketDataPort


def _klines(count: int = 60) -> list[Kline]:
    klines: list[Kline] = []
    for index in range(count):
        close = 0.5 + index * 0.001
        klines.append(
            Kline(
                symbol=DEFAULT_SYMBOL,
                timeframe="1m",
                open=close - 0.001,
                high=close + 0.002,
                low=close - 0.002,
                close=close,
                volume=10.0,
                timestamp=1_700_000_000_000 + index * 60_000,
                finished=True,
            )
        )
    return klines


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "smart_trader.db"


@pytest.fixture
def journal(db_path: Path) -> Iterator[OrderJournal]:
    instance = OrderJournal(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def reader(db_path: Path, journal: OrderJournal) -> Iterator[OrderJournalReader]:
    instance = OrderJournalReader(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def commands(db_path: Path, journal: OrderJournal) -> Iterator[CommandStore]:
    instance = CommandStore(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def client(
    reader: OrderJournalReader, commands: CommandStore
) -> Iterator[TestClient]:
    market = FakeMarketDataPort(klines=_klines())
    provider = MarketDataProvider(lambda: market)
    with TestClient(
        create_app(reader, commands, market_provider=provider)
    ) as instance:
        yield instance


def test_chart_returns_candles_and_direction(client: TestClient):
    response = client.get(
        "/api/market/chart",
        params={"symbol": "DOGE/USDT", "timeframe": "1m", "limit": 60},
    )

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["symbol"] == DEFAULT_SYMBOL.ccxt()
    assert len(body["candles"]) == 60
    assert len(body["indicator"]) == 60
    assert body["direction"] in {"long", "short", "neutral"}
    assert body["last_price"] == body["candles"][-1]["close"]


def test_missing_market_provider_returns_503(
    reader: OrderJournalReader, commands: CommandStore
):
    with TestClient(create_app(reader, commands)) as client:
        response = client.get(
            "/api/market/chart", params={"symbol": "DOGE/USDT"}
        )

    assert response.status_code == 503


def test_market_provider_failure_returns_503(
    reader: OrderJournalReader, commands: CommandStore
):
    def fail() -> MarketDataPort:
        raise RuntimeError("binance down")

    with TestClient(
        create_app(
            reader, commands, market_provider=MarketDataProvider(fail)
        )
    ) as client:
        response = client.get(
            "/api/market/chart", params={"symbol": "DOGE/USDT"}
        )

    assert response.status_code == 503
    assert "不可用" in cast(dict[str, Any], response.json())["detail"]


@pytest.mark.parametrize("symbol", ["DOGEUSDT", "DOGE", "DOGE/USDT/XX"])
def test_invalid_symbol_returns_422(client: TestClient, symbol: str):
    response = client.get(
        "/api/market/chart", params={"symbol": symbol}
    )

    assert response.status_code == 422


def test_limit_out_of_range_returns_422(client: TestClient):
    response = client.get(
        "/api/market/chart", params={"symbol": "DOGE/USDT", "limit": 0}
    )

    assert response.status_code == 422
