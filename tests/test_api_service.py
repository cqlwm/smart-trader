"""只读 API 服务层测试: 派生视图 / 聚合 / 过滤 / 分页 / 告警"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smart_trader.api.service import (
    build_overview,
    get_position_detail,
    list_order_views,
    list_position_views,
    list_strategy_summaries,
)
from smart_trader.model import (
    OrderSide,
    OrderStatus,
    PositionAckStatus,
    PositionCloseReason,
    PositionSide,
    ReconcileSnapshot,
    StrategyRuntime,
)
from smart_trader.runtime import RuntimeSnapshot
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.storage.records import PositionAckRecord
from tests.api_harness import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    OTHER_STRATEGY_KEY,
    OTHER_SYMBOL,
    make_order,
    make_position,
    seed_position,
)


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


def _long_round_trip(
    journal: OrderJournal,
    position_id: str = "POS1",
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
    symbol: str = DEFAULT_SYMBOL,
    entry_price: float = 0.5,
    exit_price: float = 0.6,
    quantity: float = 1.0,
) -> None:
    """一个已完全平仓的多头仓位"""
    seed_position(
        journal,
        make_position(position_id, strategy_key=strategy_key, symbol=symbol),
        [
            make_order(
                f"buy-{position_id}",
                position_id,
                strategy_key=strategy_key,
                symbol=symbol,
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=quantity,
                filled_qty=quantity,
                avg_price=entry_price,
            ),
            make_order(
                f"sell-{position_id}",
                position_id,
                strategy_key=strategy_key,
                symbol=symbol,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=quantity,
                filled_qty=quantity,
                avg_price=exit_price,
            ),
        ],
    )


def test_position_view_derives_quantity_price_phase_and_pnl(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=2.0,
                filled_qty=2.0,
                avg_price=0.5,
            ),
            make_order(
                "sell1",
                "POS1",
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.6,
            ),
        ],
    )

    view = list_position_views(reader)[0]

    assert view.quantity == pytest.approx(1.0), "数量 = 入场成交 2 - 出场成交 1"
    assert view.price == pytest.approx(0.5), "均价只由入场成交派生"
    assert view.realized_pnl == pytest.approx(0.1), "(0.6 - 0.5) × 1"
    assert view.phase == "holding", "有持仓且无待成交出场单"
    assert view.entry_filled == pytest.approx(2.0)
    assert view.exit_filled == pytest.approx(1.0)
    assert view.order_count == 2
    assert view.open_order_count == 0


def test_position_phase_is_closing_when_exit_order_pending(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            ),
            make_order(
                "sell1",
                "POS1",
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=1.0,
                filled_qty=0.0,
                price=0.6,
            ),
        ],
    )

    view = list_position_views(reader)[0]

    assert view.phase == "closing", "存在未成交出场单 → closing"
    assert view.pending_exit is not None
    assert view.pending_exit.order_id == "sell1"
    assert view.open_order_count == 1


def test_position_stop_loss_price_uses_fixed_rate_for_long(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position(
            "POS1", enable_stop_loss=True, stop_loss_rate=0.1
        ),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )

    view = list_position_views(reader)[0]

    assert view.stop_loss_price == pytest.approx(0.45), "多头固定止损 = 均价 × (1 - rate)"


def test_position_stop_loss_price_uses_fixed_rate_for_short(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position(
            "POS1",
            position_side=PositionSide.SHORT,
            main_order_side=OrderSide.SELL,
            enable_stop_loss=True,
            stop_loss_rate=0.1,
        ),
        [
            make_order(
                "sell1",
                "POS1",
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )

    view = list_position_views(reader)[0]

    assert view.stop_loss_price == pytest.approx(0.55), "空头固定止损 = 均价 × (1 + rate)"


def test_position_stop_loss_price_prefers_trailing_snapshot(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position(
            "POS1",
            enable_stop_loss=True,
            stop_loss_rate=0.1,
            enable_trailing_stop=True,
            trailing_stop_price=0.47,
        ),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )

    view = list_position_views(reader)[0]

    assert view.stop_loss_price == pytest.approx(0.47), "跟踪止损读 ratchet 快照"


def test_position_stop_loss_price_is_none_when_disabled(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )

    assert list_position_views(reader)[0].stop_loss_price is None


def test_take_profit_price_reflects_position_side(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1", fixed_take_profit_rate=0.1),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )
    seed_position(
        journal,
        make_position(
            "POS2",
            strategy_key=OTHER_STRATEGY_KEY,
            symbol=OTHER_SYMBOL,
            position_side=PositionSide.SHORT,
            main_order_side=OrderSide.SELL,
            fixed_take_profit_rate=0.1,
        ),
        [
            make_order(
                "sell2",
                "POS2",
                strategy_key=OTHER_STRATEGY_KEY,
                symbol=OTHER_SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            )
        ],
    )

    views = {view.id: view for view in list_position_views(reader)}

    assert views["POS1"].take_profit_price == pytest.approx(0.55)
    assert views["POS2"].take_profit_price == pytest.approx(0.45)


def test_position_entry_pending_sums_unsettled_entry_orders(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            ),
            make_order(
                "buy2",
                "POS1",
                status=OrderStatus.OPEN,
                quantity=2.0,
                filled_qty=1.0,
                price=0.4,
            ),
            make_order(
                "buy3",
                "POS1",
                status=None,
                quantity=3.0,
                filled_qty=0.0,
                price=0.3,
            ),
        ],
    )

    view = list_position_views(reader)[0]

    assert view.entry_pending == pytest.approx(4.0), "未了结入场单剩余 = (2-1) + (3-0)"


def test_list_positions_filters_by_strategy_key(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "POS1", strategy_key=DEFAULT_STRATEGY_KEY)
    _long_round_trip(
        journal,
        "POS2",
        strategy_key=OTHER_STRATEGY_KEY,
        symbol=OTHER_SYMBOL,
        entry_price=1.0,
        exit_price=1.1,
    )

    views = list_position_views(reader, strategy_key=OTHER_STRATEGY_KEY)

    assert [view.id for view in views] == ["POS2"], "策略键必须隔离"


def test_list_positions_excludes_archived_by_default(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "POS1")
    journal.close_position("POS1", PositionCloseReason.EXIT_SETTLED)

    assert list_position_views(reader) == [], "默认只返回 active=1"

    archived = list_position_views(reader, include_closed=True)
    assert [view.id for view in archived] == ["POS1"]
    assert archived[0].active is False
    assert archived[0].closed_reason == PositionCloseReason.EXIT_SETTLED
    assert archived[0].phase == "closed"


def test_order_role_is_derived_from_position_side(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position(
            "POS1",
            position_side=PositionSide.SHORT,
            main_order_side=OrderSide.SELL,
        ),
        [
            make_order(
                "sell1",
                "POS1",
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
            ),
            make_order(
                "buy1",
                "POS1",
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.4,
            ),
        ],
    )

    page = list_order_views(reader)
    roles = {order.order_id: order.role for order in page.items}

    assert roles == {"sell1": "entry", "buy1": "exit"}, "空头 sell 是入场, buy 是出场"
    assert page.items[0].symbol == DEFAULT_SYMBOL


def test_orders_can_filter_unconfirmed_wal_rows(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order("buy1", "POS1", status=None),
            make_order("buy2", "POS1", status=OrderStatus.OPEN),
        ],
    )

    page = list_order_views(reader, unconfirmed=True)

    assert page.total == 1
    assert [order.order_id for order in page.items] == ["buy1"]
    assert page.items[0].unconfirmed is True


def test_orders_filter_by_status(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order("buy1", "POS1", status=None),
            make_order("buy2", "POS1", status=OrderStatus.OPEN),
            make_order("buy3", "POS1", status=OrderStatus.CLOSED, filled_qty=10.0),
        ],
    )

    page = list_order_views(reader, status=OrderStatus.CLOSED)

    assert page.total == 1
    assert [order.order_id for order in page.items] == ["buy3"]


def test_orders_pagination_reports_total_and_slice(
    journal: OrderJournal, reader: OrderJournalReader
):
    orders = [
        make_order(
            f"buy{index}",
            "POS1",
            status=OrderStatus.OPEN,
            created_at=f"2026-01-01T00:00:0{index}+00:00",
        )
        for index in range(1, 6)
    ]
    seed_position(journal, make_position("POS1"), orders)

    page = list_order_views(reader, limit=2, offset=2)

    assert page.total == 5
    assert page.limit == 2
    assert page.offset == 2
    assert [order.order_id for order in page.items] == ["buy3", "buy2"], "按创建时间倒序"


def test_overview_empty_ledger_returns_zeroes(reader: OrderJournalReader):
    overview = build_overview(reader)

    assert overview.strategy_count == 0
    assert overview.active_position_count == 0
    assert overview.archived_position_count == 0
    assert overview.open_order_count == 0
    assert overview.unconfirmed_order_count == 0
    assert overview.realized_pnl_total == 0.0
    assert overview.realized_pnl_active == 0.0
    assert overview.strategies == []
    assert overview.alerts == []


def test_overview_aggregates_counts_pnl_and_alerts(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "ACTIVE", entry_price=0.5, exit_price=0.6)
    _long_round_trip(
        journal,
        "CLOSED",
        strategy_key=OTHER_STRATEGY_KEY,
        symbol=OTHER_SYMBOL,
        entry_price=1.0,
        exit_price=1.1,
    )
    journal.close_position("CLOSED", PositionCloseReason.EXIT_SETTLED)
    journal.append_order(
        make_order("buy-wal", "ACTIVE", status=None, quantity=1.0)
    )

    overview = build_overview(reader)

    assert overview.strategy_count == 2
    assert overview.active_position_count == 1
    assert overview.archived_position_count == 1
    assert overview.realized_pnl_total == pytest.approx(0.2), "活跃 + 归档合计"
    assert overview.realized_pnl_active == pytest.approx(0.1), "只含活跃仓位"
    assert overview.unconfirmed_order_count == 1
    assert len(overview.alerts) == 1
    assert overview.alerts[0].code == "unconfirmed_order"
    assert overview.alerts[0].order_id == "buy-wal"
    assert overview.alerts[0].severity == "critical"


def test_strategy_summary_groups_by_strategy_key(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "POS1")
    _long_round_trip(
        journal,
        "POS2",
        strategy_key=OTHER_STRATEGY_KEY,
        symbol=OTHER_SYMBOL,
        entry_price=1.0,
        exit_price=1.1,
    )
    journal.append_order(make_order("buy-open", "POS1", status=OrderStatus.OPEN))

    summaries = list_strategy_summaries(reader)
    by_key = {summary.strategy_key: summary for summary in summaries}

    assert set(by_key) == {DEFAULT_STRATEGY_KEY, OTHER_STRATEGY_KEY}
    assert by_key[DEFAULT_STRATEGY_KEY].active_positions == 1
    assert by_key[DEFAULT_STRATEGY_KEY].open_orders == 1
    assert by_key[DEFAULT_STRATEGY_KEY].realized_pnl == pytest.approx(0.1)
    assert by_key[DEFAULT_STRATEGY_KEY].symbol == DEFAULT_SYMBOL
    assert by_key[OTHER_STRATEGY_KEY].symbol == OTHER_SYMBOL


def _runtime(
    strategy_key: str,
    *,
    heartbeat_at: str | None = None,
    running: bool = True,
    symbol: str = DEFAULT_SYMBOL,
    reconcile: ReconcileSnapshot | None = None,
) -> StrategyRuntime:
    return StrategyRuntime(
        strategy_key=strategy_key,
        symbol=symbol,
        position_side=PositionSide.LONG,
        timeframe="1m",
        is_running=running,
        heartbeat_at=heartbeat_at or datetime.now(UTC).isoformat(),
        reconcile=reconcile,
    )


def _reconcile(**overrides: object) -> ReconcileSnapshot:
    fields: dict[str, object] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "exchange_qty": 3500.0,
        "local_qty": 500.0,
        "gap": -3000.0,
        "tracked_exit_orders": 1,
        "max_exit_query_failures": 0,
        "failing_exit_orders": [],
    }
    fields.update(overrides)
    return ReconcileSnapshot.model_validate(fields)


def _ack(
    *,
    external_qty: float = 3000.0,
    status: PositionAckStatus = PositionAckStatus.ACTIVE,
) -> PositionAckRecord:
    now = datetime.now(UTC).isoformat()
    return PositionAckRecord(
        id="ACK1",
        strategy_key="signal_grid_short_sell_dogeusdt_1m",
        symbol=DEFAULT_SYMBOL,
        position_side=PositionSide.LONG,
        external_qty=external_qty,
        status=status,
        acknowledged_at=now,
        updated_at=now,
    )


def _snapshot(*strategies: StrategyRuntime) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        generated_at=datetime.now(UTC).isoformat(),
        strategies=list(strategies),
    )


def test_runtime_only_strategy_is_visible_before_first_order(
    reader: OrderJournalReader,
):
    summaries = list_strategy_summaries(
        reader, _snapshot(_runtime("signal_grid_short_sell_dogeusdt_1m"))
    )

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.strategy_key == "signal_grid_short_sell_dogeusdt_1m"
    assert summary.alive is True
    assert summary.running is True
    assert summary.timeframe == "1m"
    assert summary.active_positions == 0, "尚未写账本时计数为 0"


def test_runtime_overrides_db_summary_and_marks_alive(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "POS1")

    summaries = list_strategy_summaries(
        reader, _snapshot(_runtime(DEFAULT_STRATEGY_KEY))
    )

    assert summaries[0].alive is True
    assert summaries[0].running is True
    assert summaries[0].timeframe == "1m"
    assert summaries[0].heartbeat_at is not None


def test_stale_runtime_is_offline(
    journal: OrderJournal, reader: OrderJournalReader
):
    _long_round_trip(journal, "POS1")
    stale = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()

    summaries = list_strategy_summaries(
        reader, _snapshot(_runtime(DEFAULT_STRATEGY_KEY, heartbeat_at=stale))
    )

    assert summaries[0].alive is False
    assert summaries[0].running is None
    assert summaries[0].heartbeat_at is None
    assert summaries[0].timeframe is None, "过期运行态不得覆盖账本展示字段"


def test_overview_includes_runtime_only_strategy(reader: OrderJournalReader):
    overview = build_overview(
        reader, _snapshot(_runtime("signal_grid_short_sell_dogeusdt_1m"))
    )

    assert overview.strategy_count == 1
    assert len(overview.strategies) == 1
    assert overview.strategies[0].alive is True
    assert overview.active_position_count == 0


def test_summary_exposes_reconcile_snapshot(reader: OrderJournalReader):
    summaries = list_strategy_summaries(
        reader,
        _snapshot(
            _runtime("signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile())
        ),
    )

    summary = summaries[0]
    assert summary.reconcile is not None
    assert summary.reconcile.exchange_qty == 3500.0
    assert summary.reconcile.local_qty == 500.0
    assert summary.reconcile.gap == -3000.0
    assert summary.reconcile.tracked_exit_orders == 1


def test_stale_runtime_hides_reconcile_snapshot(reader: OrderJournalReader):
    stale = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()

    summaries = list_strategy_summaries(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m",
                heartbeat_at=stale,
                reconcile=_reconcile(),
            )
        ),
    )

    assert summaries[0].reconcile is None, "过期运行态不得展示旧对账数据"


def test_overview_alerts_for_exchange_surplus(reader: OrderJournalReader):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime("signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile())
        ),
    )

    alert = next(
        item
        for item in overview.alerts
        if item.code == "exchange_position_surplus"
    )
    assert alert.severity == "critical"
    assert alert.strategy_key == "signal_grid_short_sell_dogeusdt_1m"
    assert "3000" in alert.message
    assert alert.created_at is not None


def test_overview_alerts_for_repeated_exit_query_failures(
    reader: OrderJournalReader,
):
    reconcile = _reconcile(
        max_exit_query_failures=3,
        failing_exit_orders=["exit1", "exit2"],
    )
    overview = build_overview(
        reader,
        _snapshot(
            _runtime("signal_grid_short_sell_dogeusdt_1m", reconcile=reconcile)
        ),
    )

    alert = next(
        item for item in overview.alerts if item.code == "exit_query_failing"
    )
    assert alert.severity == "warning"
    assert alert.order_id == "exit1"
    assert "3" in alert.message


@pytest.mark.parametrize(
    "reconcile",
    [
        _reconcile(gap=100.0, exchange_qty=400.0, local_qty=500.0),
        _reconcile(
            gap=0.0,
            exchange_qty=500.0,
            local_qty=500.0,
            max_exit_query_failures=2,
            failing_exit_orders=["exit1"],
        ),
    ],
)
def test_overview_skips_reconcile_alert_below_risk_threshold(
    reader: OrderJournalReader, reconcile: ReconcileSnapshot
):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime("signal_grid_short_sell_dogeusdt_1m", reconcile=reconcile)
        ),
    )

    codes = {item.code for item in overview.alerts}
    assert "exchange_position_surplus" not in codes
    assert "exit_query_failing" not in codes, "未达连续失败阈值不得告警"


def test_summary_exposes_active_ack(reader: OrderJournalReader):
    summaries = list_strategy_summaries(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile()
            )
        ),
        [_ack()],
    )

    summary = summaries[0]
    assert summary.ack is not None
    assert summary.ack.external_qty == 3000.0
    assert summary.ack.status is PositionAckStatus.ACTIVE


def test_overview_suppresses_surplus_alert_when_ack_covers_gap(
    reader: OrderJournalReader,
):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile()
            )
        ),
        [_ack()],
    )

    assert all(
        alert.code != "exchange_position_surplus" for alert in overview.alerts
    ), "人工声明覆盖全部差额时必须静默"


def test_overview_alerts_unexplained_surplus_with_partial_ack(
    reader: OrderJournalReader,
):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile()
            )
        ),
        [_ack(external_qty=2000.0)],
    )

    alert = next(
        item
        for item in overview.alerts
        if item.code == "exchange_position_surplus"
    )
    assert "未解释差额 1000" in alert.message
    assert "已标记人工 2000" in alert.message


def test_overview_alerts_stale_ack_when_overclaimed(
    reader: OrderJournalReader,
):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile()
            )
        ),
        [_ack(external_qty=4000.0)],
    )

    alert = next(
        item for item in overview.alerts if item.code == "position_ack_stale"
    )
    assert alert.severity == "warning"
    assert alert.order_id == "ACK1"
    assert "标记数量大于当前" in alert.message


def test_overview_alerts_stale_ack_when_surplus_gone(
    reader: OrderJournalReader,
):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m",
                reconcile=_reconcile(
                    exchange_qty=500.0, local_qty=500.0, gap=0.0
                ),
            )
        ),
        [_ack()],
    )

    alert = next(
        item for item in overview.alerts if item.code == "position_ack_stale"
    )
    assert "当前交易所已无多于本地的差额" in alert.message


def test_revoked_ack_does_not_suppress_alert(reader: OrderJournalReader):
    overview = build_overview(
        reader,
        _snapshot(
            _runtime(
                "signal_grid_short_sell_dogeusdt_1m", reconcile=_reconcile()
            )
        ),
        [_ack(status=PositionAckStatus.REVOKED)],
    )

    assert any(
        alert.code == "exchange_position_surplus"
        for alert in overview.alerts
    ), "已撤销声明不得继续抑制告警"


def test_position_detail_returns_orders_and_missing_returns_none(
    journal: OrderJournal, reader: OrderJournalReader
):
    seed_position(
        journal,
        make_position("POS1"),
        [
            make_order(
                "buy1",
                "POS1",
                status=OrderStatus.CLOSED,
                quantity=1.0,
                filled_qty=1.0,
                avg_price=0.5,
                created_at="2026-01-01T00:00:01+00:00",
            ),
            make_order(
                "sell1",
                "POS1",
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=1.0,
                price=0.6,
                created_at="2026-01-01T00:00:02+00:00",
            ),
        ],
    )

    detail = get_position_detail(reader, "POS1")

    assert detail is not None
    assert [order.order_id for order in detail.orders] == ["buy1", "sell1"]
    assert get_position_detail(reader, "missing") is None
