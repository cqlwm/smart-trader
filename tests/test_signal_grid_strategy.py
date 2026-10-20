"""SignalGridStrategy 完成 K 线主流程测试

覆盖: 开仓条件、平仓优先于开仓、达到上限止损、孤儿回收。
用 tests.fakes 的内存端口 + :memory: 账本, 不触网、不落盘。
"""

from datetime import UTC, datetime

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import AccountPort
from smart_trader.model import (
    Kline,
    OrderResult,
    OrderSide,
    OrderStatus,
    PlaceOrderBehavior,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fakes import (
    DEFAULT_STRATEGY_KEY,
    DEFAULT_SYMBOL,
    FakeAccountPort,
    FakeMarketDataPort,
    FakeTradingPort,
    LedgerMirroringAccountPort,
    make_exchange_position,
    make_order_record,
    make_position_row,
)

TIMEFRAME = "1m"
TIMESTAMP = 1_700_000_000_000


def build_strategy(
    *,
    journal: OrderJournal | None = None,
    account: AccountPort | None = None,
    **overrides: object,
) -> tuple[SignalGridStrategy, FakeTradingPort, OrderJournal, AccountPort]:
    """组装一个全内存的策略实例

    @param journal 注入已有账本(用于验证启动加载); 默认新建空账本
    @param account 注入账户端口(用于预设交易所持仓); 默认镜像账本持仓
    """
    fields: dict[str, object] = {
        "symbol": DEFAULT_SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy_key": DEFAULT_STRATEGY_KEY,
        "per_order_qty": 100.0,
        "grid_spacing_rate": 0.01,
        # NORMAL 直接走 TradingPort.place, 避免追单器引入额外分支
        "place_order_behavior": PlaceOrderBehavior.NORMAL,
    }
    fields.update(overrides)
    config = SignalGridStrategyConfig.model_validate(fields)
    trading = FakeTradingPort()
    if journal is None:
        journal = OrderJournal(":memory:")
    if account is None:
        # 默认镜像账本仓位: 表达"本地 = 交易所, 无外部偏差"测试前提
        account = LedgerMirroringAccountPort(journal, DEFAULT_STRATEGY_KEY, DEFAULT_SYMBOL)
    strategy = SignalGridStrategy(config, trading, FakeMarketDataPort(), journal, account)
    return strategy, trading, journal, account


def push_kline(
    strategy: SignalGridStrategy,
    close: float,
    high: float | None = None,
    low: float | None = None,
    timestamp: int = TIMESTAMP,
) -> None:
    """推进一根已收盘的 K 线（走 run() 公开入口）

    @param timestamp 默认固定值; 需要推进 K 线时间时传入不同值
    """
    strategy.run(
        Kline(
            symbol=DEFAULT_SYMBOL,
            timeframe=TIMEFRAME,
            open=close,
            high=close if high is None else high,
            low=close if low is None else low,
            close=close,
            volume=0.0,
            timestamp=timestamp,
            finished=True,
        )
    )


def seed_position(
    strategy: SignalGridStrategy,
    position_id: str = "POS1",
    *,
    entry_price: float = 0.5,
    quantity: float = 100.0,
    **row_overrides: object,
) -> GridPosition:
    """放入一个逻辑仓位: 附带一条按 `quantity` 成交的入场单（内存 + 账本）"""
    row = make_position_row(position_id, **row_overrides)
    entry_side = (
        OrderSide.BUY if row.position_side == PositionSide.LONG else OrderSide.SELL
    )
    order = make_order_record(
        f"o-{position_id}",
        position_id,
        side=entry_side,
        price=entry_price,
        quantity=quantity,
        filled_qty=quantity,
        avg_price=entry_price,
        status=OrderStatus.CLOSED,
    )
    position = GridPosition.from_row(row, [order])
    strategy.journal.upsert_position(row)
    strategy.journal.append_order(order)
    strategy.add_position(position)
    return position


# ------------------------------------------------------------------ 启动加载


def test_restores_positions_from_journal_on_startup():
    """构造时必须从账本恢复活跃仓位, 否则策略会忘记重启前的敞口"""
    journal = OrderJournal(":memory:")
    journal.upsert_position(make_position_row("POS9"))
    journal.append_order(
        make_order_record(
            "o-POS9",
            "POS9",
            side=OrderSide.BUY,
            quantity=10.0,
            filled_qty=10.0,
            avg_price=0.5,
            status=OrderStatus.CLOSED,
        )
    )

    strategy, _, _, _ = build_strategy(journal=journal)

    assert [position.id for position in strategy.positions] == ["POS9"]
    assert strategy.positions[0].quantity == pytest.approx(10.0), (
        "关联订单必须随仓位一起还原"
    )


# ------------------------------------------------------------------ 开仓条件


def test_opens_position_when_grid_spacing_reached():
    """空仓位时, 价格入区间即开仓"""
    strategy, trading, _, _ = build_strategy()

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_skips_open_when_price_inside_grid_spacing():
    """价格未走出网格间距时不开仓"""
    strategy, trading, _, _ = build_strategy()
    seed_position(strategy)

    push_kline(strategy, 0.5005)  # 0.1% < 1% 间距

    assert len(strategy.positions) == 1
    assert trading.placed == []


def test_negative_spacing_adds_position_in_profit_direction():
    """负间距(双向距离限制): 盈利方向距离内仍加仓 —— 顺势加仓的既有用法"""
    strategy, trading, _, _ = build_strategy(grid_spacing_rate=-0.1)
    seed_position(strategy)

    push_kline(strategy, 0.525)  # +5%: ratio 0.05 <= 0.1

    assert len(strategy.positions) == 2, "盈利方向距离内应放行加仓"
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_negative_spacing_allows_exactly_at_distance():
    """负间距含等号: 涨跌幅恰好等于 |rate| 时放行"""
    strategy, _, _, _ = build_strategy(grid_spacing_rate=-0.1)
    seed_position(strategy)

    push_kline(strategy, 0.55)  # +10%: ratio 恰好等于距离阈值

    assert len(strategy.positions) == 2, "恰好等于 |rate| 距离应放行(含等号)"


def test_negative_spacing_blocks_beyond_distance():
    """负间距: 涨跌幅超出 |rate| 距离后拦截"""
    strategy, trading, _, _ = build_strategy(grid_spacing_rate=-0.1)
    seed_position(strategy)

    push_kline(strategy, 0.61)  # +22%: ratio 0.22 > 0.1

    assert len(strategy.positions) == 1, "超出距离阈值应拦截"
    assert trading.placed == []


def test_trend_spacing_adds_layer_after_profit_spacing():
    """顺势间距: 已有多层时, 盈利方向恰好走出间距才加仓"""
    strategy, trading, _, _ = build_strategy(trend_spacing_rate=0.01)
    seed_position(strategy, "INNER", entry_price=0.5, quantity=100.0)
    seed_position(strategy, "OUTER", entry_price=0.52, quantity=100.0)

    push_kline(strategy, 0.525, timestamp=TIMESTAMP)  # +0.96% < 1%
    assert len(strategy.positions) == 2, "未走出间距不应加仓"

    push_kline(strategy, 0.5252, timestamp=TIMESTAMP + 60_000)  # +1% 恰好
    assert len(strategy.positions) == 3, "恰好走出间距应顺势加仓"
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_trend_spacing_blocks_loss_direction():
    """顺势间距: 已有多层时亏损方向不得加仓（与网格间距的关键差异）

    同样的 -10% 行情在 `grid_spacing_rate=0.01`（网格口径）下会加仓,
    顺势口径必须拦截 —— 只顺盈利方向加层。
    """
    strategy, trading, _, _ = build_strategy(trend_spacing_rate=0.01)
    seed_position(strategy, "LOW", entry_price=0.5, quantity=100.0)
    seed_position(strategy, "HIGH", entry_price=0.6, quantity=100.0)

    push_kline(strategy, 0.45)

    assert len(strategy.positions) == 2, "顺势闸门不得在亏损方向加仓"
    assert trading.placed == []


def test_trend_spacing_allows_second_layer_in_loss_direction():
    """只有一层已成交仓位时, 第二层逆势也应加仓"""
    strategy, trading, _, _ = build_strategy(trend_spacing_rate=0.01)
    seed_position(strategy)

    push_kline(strategy, 0.495)

    assert len(strategy.positions) == 2, "只有一层时第二层逆势也应加仓"
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_skips_open_when_price_outside_allowed_range():
    """价格超出可交易区间时不开仓"""
    strategy, trading, _, _ = build_strategy(lowest_price=0.4, highest_price=0.6)

    push_kline(strategy, 0.7)

    assert strategy.positions == []
    assert trading.placed == []


def test_opens_when_kline_has_no_close_action():
    """本根 K 线没有平仓动作时, 开仓照常执行"""
    strategy, trading, _, _ = build_strategy()
    seed_position(strategy)  # 未启用止损

    push_kline(strategy, 0.47)  # 跌破 6%, 但不构成平仓条件

    assert len(strategy.positions) == 2
    assert [request.side for request in trading.placed] == [OrderSide.BUY]


def test_empty_position_uses_opening_price_as_grid_reference():
    """空仓位以 opening_price 作为网格基准（无成交时的参考价）"""
    strategy, trading, _, _ = build_strategy()
    position = seed_position(strategy, quantity=0.0, opening_price=0.5)

    assert position.price == pytest.approx(0.5)
    assert position.quantity == 0.0

    push_kline(strategy, 0.4995)  # 0.1% < 1% 间距

    assert len(strategy.positions) == 1
    assert trading.placed == []


def test_zero_per_order_qty_registers_position_without_order():
    """per_order_qty=0: 只登记空仓位(网格参考), 不发任何订单"""
    strategy, trading, journal, _ = build_strategy(per_order_qty=0)

    push_kline(strategy, 0.5)

    assert trading.placed == [], "零数量配置只登记仓位, 不得发单"
    assert len(strategy.positions) == 1
    position = strategy.positions[0]
    assert position.quantity == 0.0, "零数量仓位无任何成交"
    assert position.phase == "opening"
    assert position.price == pytest.approx(0.5), "opening_price 作为网格参考基准"

    rows = journal.load_active_positions(DEFAULT_STRATEGY_KEY)
    assert len(rows) == 1, "空仓位必须落库, 重启后仍可用作网格参考"
    assert rows[0].opening_price == pytest.approx(0.5)
    assert journal.load_orders_for_positions([rows[0].id]) == [], (
        "零数量分支不产生任何订单行"
    )


def test_zero_per_order_qty_respects_grid_spacing():
    """per_order_qty=0: 闸门链照常生效, 间距内不重复登记空仓位"""
    strategy, trading, _, _ = build_strategy(per_order_qty=0)

    push_kline(strategy, 0.5)
    push_kline(strategy, 0.5005, timestamp=TIMESTAMP + 60_000)  # 0.1% < 1% 间距

    assert len(strategy.positions) == 1, "间距内不得重复登记空仓位"
    assert trading.placed == []


# ------------------------------------------------------------- 离场上下文契约


def test_exit_context_without_kline_raises():
    """尚无行情时 _exit_context 抛 MARKET_DATA_UNAVAILABLE, 禁止以 0.0 静默兜底"""
    strategy, _, _, _ = build_strategy()

    with pytest.raises(SmartTraderError) as exc_info:
        strategy._exit_context()

    assert exc_info.value.error_code is ErrorCode.MARKET_DATA_UNAVAILABLE, (
        "无行情应抛 MARKET_DATA_UNAVAILABLE, 而非以 close=0.0 构造上下文"
    )


# ------------------------------------------------------- 平仓优先于开仓（互斥）


def test_close_takes_priority_over_open_on_stop_loss():
    """止损触发时先平仓; 本根 K 线不得再开新仓"""
    strategy, trading, _, _ = build_strategy(
        enable_order_stop_loss=True, order_stop_loss_rate=0.05
    )
    seed_position(
        strategy,
        enable_stop_loss=True,
        stop_loss_rate=0.05,
        trailing_stop_price=0.475,
    )

    push_kline(strategy, 0.47)  # 跌破止损价, 同时满足补仓间距

    assert [request.side for request in trading.placed] == [OrderSide.SELL], (
        "本根 K 线只应挂出平仓单"
    )
    assert len(strategy.positions) == 1, "平仓当根不得再开新仓"
    assert strategy.positions[0].pending_exit_order is not None


def test_fixed_take_profit_places_exit_and_skips_open():
    """固定止盈触发时同样平仓优先"""
    strategy, trading, _, _ = build_strategy(
        fixed_rate_take_profit=True, fixed_take_profit_rate=0.01
    )
    seed_position(strategy)

    push_kline(strategy, 0.51)

    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert len(strategy.positions) == 1


# --------------------------------------------------------------- 达到上限止损


def test_stop_all_at_max_order_pauses_strategy():
    """达到上限: 全部挂平仓单 + 暂停策略"""
    strategy, trading, _, _ = build_strategy(enable_max_order_stop_loss=True, max_order=3)
    seed_position(strategy, "POS1", entry_price=0.5)
    seed_position(strategy, "POS2", entry_price=0.49)
    stop_calls: list[str] = []
    strategy.on_stop_loss_order_all = lambda: stop_calls.append("called")

    push_kline(strategy, 0.48)

    assert stop_calls == ["called"], "达到上限止损必须回调"
    assert strategy.is_running is False, "默认止损后暂停策略"
    assert [request.side for request in trading.placed] == [
        OrderSide.SELL,
        OrderSide.SELL,
    ]
    assert len(strategy.positions) == 2, "出场单未成交, 不归档"


def test_stop_all_without_pause_still_skips_open():
    """关闭暂停后 is_running 仍为真, 但本根 K 线已平仓 -> 不开新仓"""
    strategy, trading, _, _ = build_strategy(
        enable_max_order_stop_loss=True, max_order=2, paused_after_stop_loss=False
    )
    seed_position(strategy, "POS1", entry_price=0.5)

    push_kline(strategy, 0.47)

    assert strategy.is_running is True
    assert [request.side for request in trading.placed] == [OrderSide.SELL]
    assert len(strategy.positions) == 1, "平仓当根不得再开新仓"


# ------------------------------------------------------------------ 孤儿回收


def test_reaps_expired_empty_position():
    """空仓位超过阈值时长后回收（归档）"""
    strategy, _, journal, _ = build_strategy()
    row = make_position_row("POS9", created_at="2020-01-01T00:00:00+00:00")
    journal.upsert_position(row)
    strategy.add_position(GridPosition.from_row(row, []))

    push_kline(strategy, 0.5)

    assert strategy.positions == [], "超时空仓位应被回收"
    assert journal.load_active_positions(DEFAULT_STRATEGY_KEY) == [], (
        "回收即置 active=0"
    )


def test_keeps_recent_empty_position():
    """空仓位未超时则保留, 继续充当网格价格参考"""
    strategy, _, journal, _ = build_strategy()
    row = make_position_row("POS9", created_at=datetime.now(UTC).isoformat())
    journal.upsert_position(row)
    strategy.add_position(GridPosition.from_row(row, []))

    push_kline(strategy, 0.5)

    assert len(strategy.positions) == 1


# ------------------------------------------------------- 持仓对账集成（Step 27）


def test_startup_reconcile_archives_externally_closed_position():
    """启动对账: 账本有仓位但交易所已无持仓(外部被手动平掉) → 内部归档"""
    journal = OrderJournal(":memory:")
    journal.upsert_position(make_position_row("POS9"))
    journal.append_order(
        make_order_record(
            "o-POS9",
            "POS9",
            side=OrderSide.BUY,
            quantity=10.0,
            filled_qty=10.0,
            avg_price=0.5,
            status=OrderStatus.CLOSED,
        )
    )
    account = FakeAccountPort()  # 交易所无任何持仓

    build_strategy(journal=journal, account=account)

    assert journal.load_active_positions(DEFAULT_STRATEGY_KEY) == [], (
        "启动对账应把交易所已无持仓的仓位落库归档(active=0)"
    )


def test_startup_reconcile_keeps_matching_position():
    """启动对账: 持仓数量与交易所一致 → 保留"""
    journal = OrderJournal(":memory:")
    journal.upsert_position(make_position_row("POS9"))
    journal.append_order(
        make_order_record(
            "o-POS9",
            "POS9",
            side=OrderSide.BUY,
            quantity=10.0,
            filled_qty=10.0,
            avg_price=0.5,
            status=OrderStatus.CLOSED,
        )
    )
    account = FakeAccountPort()
    account.set_positions(make_exchange_position(10.0))

    strategy, _, _, _ = build_strategy(journal=journal, account=account)

    assert [position.id for position in strategy.positions] == ["POS9"]


def test_kline_reconcile_archives_externally_closed_position():
    """每根完成K线对账: 外部全平的仓位在下一根K线被内部归档"""
    account = FakeAccountPort()  # 交易所持仓被手动清空
    strategy, _, _, _ = build_strategy(account=account)
    seed_position(strategy)

    push_kline(strategy, 0.5)

    assert strategy.positions == [], "外部全平的仓位应被对账归档"


def test_exit_order_quantity_clamped_by_external_reduction():
    """外部平掉部分仓位: 退出信号触发的出场单按剩余可平量下单"""
    account = FakeAccountPort()
    strategy, trading, _, _ = build_strategy(
        account=account,
        fixed_rate_take_profit=True,
        fixed_take_profit_rate=0.01,
    )
    seed_position(strategy)  # 100 @ 0.5

    account.set_positions(make_exchange_position(60.0))  # 外部已平 40
    push_kline(strategy, 0.52)  # 收盘价触及止盈

    assert [request.quantity for request in trading.placed] == [60.0], (
        f"出场单应按交易所剩余量 60 下单, 实际 {[r.quantity for r in trading.placed]}"
    )


def test_resting_take_profit_quantity_clamped_by_external_reduction():
    """外部平掉部分仓位: 盘中挂休息止盈单前实时对账, 按剩余可平量下单"""
    account = FakeAccountPort()
    account.set_positions(make_exchange_position(60.0))  # 外部已平 40
    strategy, trading, _, _ = build_strategy(
        account=account,
        fixed_rate_take_profit=True,
        take_profit_use_limit_order=True,
    )
    seed_position(strategy)  # 100 @ 0.5

    push_kline(strategy, 0.5)  # 盘中段触发休息止盈挂单(挂单前实时对账)

    assert [request.quantity for request in trading.placed] == [60.0], (
        f"休息止盈单应按剩余可平量 60 挂出, 实际 {[r.quantity for r in trading.placed]}"
    )


def test_kline_reconcile_runs_every_finished_kline():
    """每根完成K线都执行持仓对账(含暂停状态)"""
    account = FakeAccountPort()
    account.set_positions(make_exchange_position(100.0))  # 与本地一致
    strategy, _, _, _ = build_strategy(account=account)
    seed_position(strategy)
    calls_before = account.positions_calls

    push_kline(strategy, 0.5)
    assert account.positions_calls == calls_before + 1

    strategy.is_running = False  # 模拟止损暂停
    push_kline(strategy, 0.5, timestamp=TIMESTAMP + 60_000)
    assert account.positions_calls == calls_before + 2, "暂停后仍必须对账"


def _closed_reason(journal: OrderJournal, position_id: str) -> str | None:
    """裸读归档行的 closed_reason 列"""
    from sqlalchemy import text

    with journal._engine.connect() as conn:
        row = conn.execute(
            text("SELECT closed_reason FROM grid_positions WHERE id = :id"),
            {"id": position_id},
        ).one()
        return row[0] if row[0] is None else str(row[0])


def test_external_reconciled_reason_persisted():
    """对账内部归档的仓位: closed_reason = external_reconciled"""
    account = FakeAccountPort()  # 交易所持仓被手动清空
    strategy, _, journal, _ = build_strategy(
        account=account, fixed_rate_take_profit=False
    )
    seed_position(strategy, "POS1")

    push_kline(strategy, 0.5)

    assert _closed_reason(journal, "POS1") == "external_reconciled", (
        f"外部平仓对账归档应落库 external_reconciled, "
        f"got {_closed_reason(journal, 'POS1')}"
    )


def test_exit_settled_reason_persisted():
    """出场成交正常归档的仓位: closed_reason = exit_settled"""
    strategy, trading, journal, _ = build_strategy(
        fixed_rate_take_profit=True, fixed_take_profit_rate=0.01
    )
    seed_position(strategy, "POS1")

    push_kline(strategy, 0.52)  # 触及止盈, 挂出场单
    trading.queue_query(
        OrderResult(
            custom_id="o-POS1",
            symbol=DEFAULT_SYMBOL,
            side=OrderSide.SELL,
            status=OrderStatus.CLOSED,
            quantity=100.0,
            filled_qty=100.0,
            avg_price=0.52,
        )
    )
    push_kline(strategy, 0.52, timestamp=TIMESTAMP + 60_000)  # 出场单结算归档

    assert _closed_reason(journal, "POS1") == "exit_settled", (
        f"出场结算归档应落库 exit_settled, got {_closed_reason(journal, 'POS1')}"
    )


def test_orphan_reaped_reason_persisted():
    """超时空仓位回收: closed_reason = orphan_reaped"""
    strategy, _, journal, _ = build_strategy(fixed_rate_take_profit=False)
    row = make_position_row("POS9", created_at="2020-01-01T00:00:00+00:00")
    journal.upsert_position(row)
    strategy.add_position(GridPosition.from_row(row, []))

    push_kline(strategy, 0.5)

    assert _closed_reason(journal, "POS9") == "orphan_reaped", (
        f"孤儿回收应落库 orphan_reaped, got {_closed_reason(journal, 'POS9')}"
    )


def test_entry_discarded_reason_persisted():
    """未成交入场单被出场流程撤掉后归零归档: closed_reason = entry_discarded"""
    strategy, _, journal, _ = build_strategy(
        fixed_rate_take_profit=True, fixed_take_profit_rate=0.01
    )
    row = make_position_row("POS9")
    order = make_order_record(
        "o-POS9",
        "POS9",
        side=OrderSide.BUY,
        quantity=100.0,
        filled_qty=0.0,
        status=OrderStatus.OPEN,
    )
    position = GridPosition.from_row(row, [order])
    journal.upsert_position(row)
    journal.append_order(order)
    strategy.add_position(position)

    push_kline(strategy, 0.52)  # 触及止盈 → 出场流程撤未成交入场单 → 归零归档

    assert _closed_reason(journal, "POS9") == "entry_discarded", (
        f"入场丢弃归档应落库 entry_discarded, got {_closed_reason(journal, 'POS9')}"
    )