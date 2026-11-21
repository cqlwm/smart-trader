"""配置 API 测试: 列表 / 默认值 / 创建 / 乐观锁更新 / 活跃仓位护栏 / 鉴权"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from smart_trader.api.app import create_app
from smart_trader.model import OrderStatus
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.config_store import ConfigStore
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from tests.api_harness import (
    AUTH_HEADERS,
    TEST_TOKEN,
    make_order,
    make_position,
    seed_position,
)

KEY = "timer_grid_doge_1m"


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
def configs(db_path: Path, journal: OrderJournal) -> Iterator[ConfigStore]:
    instance = ConfigStore(str(db_path))
    yield instance
    instance.close()


@pytest.fixture
def client(
    reader: OrderJournalReader,
    commands: CommandStore,
    configs: ConfigStore,
) -> Iterator[TestClient]:
    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN, config_store=configs),
        headers=AUTH_HEADERS,
    ) as instance:
        yield instance


def _create(client: TestClient, strategy_key: str = KEY, **payload: object):
    body: dict[str, object] = {
        "strategy_key": strategy_key,
        "enabled": True,
        "payload": {"symbol": "DOGE/USDT", "timeframe": "1m", **payload},
    }
    return client.post("/api/configs", json=body)


def _put(
    client: TestClient,
    *,
    strategy_key: str = KEY,
    expected_version: int = 1,
    enabled: bool = True,
    payload: dict[str, object] | None = None,
):
    body: dict[str, object] = {
        "enabled": enabled,
        "expected_version": expected_version,
        "payload": {
            "symbol": "DOGE/USDT",
            "timeframe": "1m",
            **(payload or {}),
        },
    }
    return client.put(f"/api/configs/{strategy_key}", json=body)


def _seed_active_position(journal: OrderJournal, strategy_key: str = KEY) -> None:
    seed_position(
        journal,
        make_position("POS1", strategy_key=strategy_key),
        [
            make_order(
                "buy1",
                "POS1",
                strategy_key=strategy_key,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )


def test_list_configs_returns_views(client: TestClient):
    assert _create(client).status_code == 201

    response = client.get("/api/configs")

    assert response.status_code == 200
    items = cast(list[dict[str, Any]], response.json())
    assert len(items) == 1
    assert items[0]["strategy_key"] == KEY
    assert items[0]["version"] == 1
    assert items[0]["enabled"] is True
    assert items[0]["payload"]["data_source"] == "ws"


def test_defaults_return_form_baseline(client: TestClient):
    response = client.get("/api/configs/defaults")

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["symbol"] == "DOGE/USDT"
    assert body["timeframe"] == "1m"
    assert body["data_source"] == "ws"
    assert body["signal_type"] == "alpha_trend"
    assert body["per_order_amount"] == pytest.approx(0.02)
    assert body["per_order_unit"] == "base"
    assert body["entry_mode"] == "grid"


def test_get_unknown_config_returns_404(client: TestClient):
    assert client.get("/api/configs/missing").status_code == 404


def test_post_creates_version_one(client: TestClient):
    response = _create(client, max_order=7)

    assert response.status_code == 201
    body = cast(dict[str, Any], response.json())
    assert body["strategy_key"] == KEY
    assert body["version"] == 1
    assert body["enabled"] is True
    assert body["payload"]["max_order"] == 7


def test_post_accepts_inverted_alpha_trend_signal(client: TestClient):
    response = _create(client, signal_type="alpha_trend_inverse")

    assert response.status_code == 201
    body = cast(dict[str, Any], response.json())
    assert body["payload"]["signal_type"] == "alpha_trend_inverse"


def test_post_accepts_quote_notional_size(client: TestClient):
    response = _create(client, per_order_amount=100, per_order_unit="quote")

    assert response.status_code == 201
    body = cast(dict[str, Any], response.json())
    assert body["payload"]["per_order_amount"] == pytest.approx(100)
    assert body["payload"]["per_order_unit"] == "quote"


def test_post_accepts_trend_spacing_with_empty_grid_spacing(client: TestClient):
    """只填顺势间距: 网格间距留空（null）必须可创建"""
    response = _create(
        client,
        entry_mode="trend",
        grid_spacing_rate=None,
        trend_spacing_rate=0.0002,
    )

    assert response.status_code == 201
    body = cast(dict[str, Any], response.json())
    assert body["payload"]["entry_mode"] == "trend"
    assert body["payload"]["grid_spacing_rate"] is None
    assert body["payload"]["trend_spacing_rate"] == pytest.approx(0.0002)


def test_put_accepts_trend_spacing_with_empty_grid_spacing(client: TestClient):
    """编辑策略: 切换为顺势模式并清空网格间距必须可保存"""
    _create(client)

    response = _put(
        client,
        payload={
            "entry_mode": "trend",
            "grid_spacing_rate": None,
            "trend_spacing_rate": 0.0002,
        },
    )

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["payload"]["entry_mode"] == "trend"
    assert body["payload"]["grid_spacing_rate"] is None
    assert body["payload"]["trend_spacing_rate"] == pytest.approx(0.0002)


def test_post_rejects_trend_mode_without_trend_spacing(client: TestClient):
    """entry_mode=trend 但未填顺势间距视为配置错误"""
    response = _create(client, entry_mode="trend", grid_spacing_rate=None)

    assert response.status_code == 422
    assert "trend_spacing_rate 必填" in response.text


def test_positions_hide_take_profit_line_when_fixed_take_profit_disabled(
    client: TestClient, journal: OrderJournal
):
    """配置未启用固定止盈时, 仓位页不得出现止盈线"""
    assert _create(client).status_code == 201
    _seed_active_position(journal)

    items = cast(list[dict[str, Any]], client.get("/api/positions").json())
    assert items[0]["take_profit_price"] is None

    detail = cast(dict[str, Any], client.get("/api/positions/POS1").json())
    assert detail["take_profit_price"] is None


def test_positions_show_take_profit_line_when_fixed_take_profit_enabled(
    client: TestClient, journal: OrderJournal
):
    """启用固定止盈时止盈线按仓位费率正常展示"""
    assert _create(client, fixed_rate_take_profit=True).status_code == 201
    _seed_active_position(journal)

    items = cast(list[dict[str, Any]], client.get("/api/positions").json())

    assert items[0]["take_profit_price"] == pytest.approx(0.505), (
        "0.5 均价 × (1 + 0.01) = 0.505"
    )


def test_post_duplicate_key_returns_409(client: TestClient):
    _create(client)

    response = _create(client)

    assert response.status_code == 409
    assert "已存在" in cast(dict[str, Any], response.json())["detail"]


def test_post_invalid_payload_returns_422(client: TestClient):
    response = client.post(
        "/api/configs",
        json={
            "strategy_key": KEY,
            "payload": {"symbol": "BAD"},
        },
    )

    assert response.status_code == 422


def test_put_updates_and_increments_version(client: TestClient):
    _create(client)

    response = _put(client, payload={"max_order": 9})

    assert response.status_code == 200
    body = cast(dict[str, Any], response.json())
    assert body["version"] == 2, "更新成功后版本必须递增"
    assert body["payload"]["max_order"] == 9
    assert client.get(f"/api/configs/{KEY}").json()["payload"]["max_order"] == 9


def test_put_stale_version_returns_409(client: TestClient):
    _create(client)
    _put(client, payload={"max_order": 9})

    response = _put(client, expected_version=1, payload={"max_order": 11})

    assert response.status_code == 409
    assert "版本冲突" in cast(dict[str, Any], response.json())["detail"]


def test_put_unknown_key_returns_404(client: TestClient):
    assert _put(client, strategy_key="missing").status_code == 404


def test_disable_with_active_positions_returns_409(
    client: TestClient, journal: OrderJournal
):
    _create(client)
    _seed_active_position(journal)

    response = _put(client, enabled=False)

    assert response.status_code == 409
    assert "enabled" in cast(dict[str, Any], response.json())["detail"]
    assert client.get(f"/api/configs/{KEY}").json()["enabled"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "ZEC/USDT"),
        ("timeframe", "5m"),
        ("data_source", "timer"),
        ("position_side", "short"),
        ("master_side", "sell"),
    ],
)
def test_identity_change_with_active_positions_returns_409(
    client: TestClient, journal: OrderJournal, field: str, value: str
):
    _create(client)
    _seed_active_position(journal)

    response = _put(client, payload={field: value})

    assert response.status_code == 409, f"活跃仓位下禁止修改 {field}"
    detail = cast(dict[str, Any], response.json())["detail"]
    assert field in detail


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "ZEC/USDT"),
        ("timeframe", "5m"),
        ("data_source", "timer"),
        ("position_side", "short"),
        ("master_side", "sell"),
    ],
)
def test_immutable_field_change_without_active_positions_returns_409(
    client: TestClient, field: str, value: str
):
    """身份字段创建后始终不可变, 不依赖是否存在活跃仓位"""
    _create(client)

    response = _put(client, payload={field: value})

    assert response.status_code == 409, f"身份字段 {field} 创建后不可修改"
    detail = cast(dict[str, Any], response.json())["detail"]
    assert field in detail

    current = cast(dict[str, Any], client.get(f"/api/configs/{KEY}").json())
    assert current["version"] == 1, "拒绝后不得递增版本"
    assert current["payload"][field] != value, "拒绝后不得写入新值"


def test_tuning_parameter_change_with_active_positions_is_allowed(
    client: TestClient, journal: OrderJournal
):
    _create(client)
    _seed_active_position(journal)

    response = _put(
        client, payload={"max_order": 12, "order_stop_loss_rate": 0.08}
    )

    assert response.status_code == 200
    assert cast(dict[str, Any], response.json())["version"] == 2


def test_disable_without_active_positions_is_allowed(client: TestClient):
    _create(client)

    response = _put(client, enabled=False)

    assert response.status_code == 200
    assert cast(dict[str, Any], response.json())["enabled"] is False


def test_delete_config_is_not_allowed(client: TestClient):
    _create(client)

    assert client.delete(f"/api/configs/{KEY}").status_code == 405


def test_configs_require_token(
    reader: OrderJournalReader, commands: CommandStore, configs: ConfigStore
):
    with TestClient(
        create_app(reader, commands, config_store=configs, token="secret")
    ) as guarded:
        assert guarded.get("/api/configs").status_code == 401

        ok = guarded.get(
            "/api/configs", headers={"Authorization": "Bearer secret"}
        )
        assert ok.status_code == 200


def test_configs_return_503_without_store(
    reader: OrderJournalReader, commands: CommandStore
):
    with TestClient(
        create_app(reader, commands, token=TEST_TOKEN), headers=AUTH_HEADERS
    ) as bare:
        assert bare.get("/api/configs").status_code == 503
