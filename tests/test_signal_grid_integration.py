"""SignalGridStrategy 集成测试: 行为端到端 + 账本不变量

真实装配链(策略 + Gateway + JournaledTradingPort + OrderReconciler)
对接真实 sqlite 文件账本与状态化交易所, 用确定性 K 线驱动。

两个观察通道交叉断言:
- 交易所订单簿(StatefulFakeExchange.book) —— 策略让交易所做了什么;
- 账本磁盘行(sqlite3 独立连接) —— 策略声称发生了什么。
"""

from pathlib import Path
from typing import Any

import pytest

from smart_trader.model import OrderSide, PositionSide
from smart_trader.storage.order_journal import OrderJournal
from tests.fake_exchange import StatefulFakeExchange
from tests.integration_harness import (
    DEFAULT_STRATEGY_KEY,
    KlineDriver,
    assert_ledger_matches_exchange,
    make_strategy,
    read_orders,
    read_pending_orders,
    read_positions,
)

LONG_ENTRY = OrderSide.BUY
LONG_EXIT = OrderSide.SELL


def book_ids_by_side(exchange: StatefulFakeExchange, side: OrderSide) -> list[str]:
    """簿内指定方向的订单 id(按入簿序)"""
    return [key for key, entry in exchange.book.items() if entry.side is side]


def ledger_side_rows(
    db_path: str | Path, side: OrderSide, status: str | None = None
) -> list[dict[str, Any]]:
    """账本内指定方向(可选再按状态过滤)的订单行"""
    rows = [
        row
        for row in read_orders(db_path, DEFAULT_STRATEGY_KEY)
        if row["side"] == side.value
    ]
    if status is None:
        return rows
    return [row for row in rows if row["status"] == status]


@pytest.fixture()
def ledger(tmp_path_factory: pytest.TempPathFactory) -> str:
    """每个用例独立的 sqlite 文件账本"""
    return str(tmp_path_factory.mktemp("ledger") / "smart_trader.db")


# ------------------------------------------------------------------ A. 行为端到端


class TestBehaviorEndToEnd:
    """行为正确性: K 线驱动完整网格动作, 双通道(簿 + 账本)断言"""

    def test_first_entry_writes_position_and_order_atomically(self, ledger: str) -> None:
        """首根 K 线开仓: 仓位行与首单行同事务落库, 簿内挂单一致"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        driver.push(0.5)

        entry_ids = book_ids_by_side(exchange, LONG_ENTRY)
        assert entry_ids == list(exchange.book), "首根 K 线应只挂一笔入场单"
        assert exchange.book[entry_ids[0]].quantity == 100.0
        assert exchange.book[entry_ids[0]].price == 0.5

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1, f"应有一条仓位行, got {len(positions)}"
        assert positions[0]["active"] == 1, "新仓位应为活跃"
        assert positions[0]["opening_price"] == 0.5, "开仓参考价应为当根收盘价"

        rows = ledger_side_rows(ledger, LONG_ENTRY)
        assert len(rows) == 1, f"应有一条入场单行, got {len(rows)}"
        assert rows[0]["status"] == "open", "挂单应回写为 open"
        assert rows[0]["grid_position_id"] == positions[0]["id"], (
            "首单必须与仓位行同事务关联(避免孤儿)"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_entry_fill_then_spacing_adds_second_position(self, ledger: str) -> None:
        """入场成交后价格走出间距(恰为 -1%, 含等号)再加仓第二仓位"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.495)

        entry_ids = book_ids_by_side(exchange, LONG_ENTRY)
        assert len(entry_ids) == 2, "走出间距后应加仓第二笔入场单"
        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert len(positions) == 2, f"应有两条仓位行, got {len(positions)}"
        rows = ledger_side_rows(ledger, LONG_ENTRY, "open")
        assert len(rows) == 2, "两笔入场单都应挂在簿"
        assert {row["price"] for row in rows} == {0.5, 0.495}, "两笔入场价应为 0.5 与 0.495"
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_take_profit_places_exit_and_archives(self, ledger: str) -> None:
        """价格涨过固定止盈率(严格 >) → 挂出场单; 成交后归档并按格重开"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange, OrderJournal(ledger), grid_spacing_rate=0.5
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        entry_id = book_ids_by_side(exchange, LONG_ENTRY)[0]
        exchange.fill(entry_id, 100.0, avg_price=0.5)
        driver.push(0.52)

        exit_ids = book_ids_by_side(exchange, LONG_EXIT)
        assert len(exit_ids) == 1, "达到止盈档应挂一笔出场单"
        assert exchange.book[exit_ids[0]].quantity == 100.0, "出场量应等于持仓派生量"
        assert exchange.book[exit_ids[0]].price == 0.52

        exchange.fill(exit_ids[0], 100.0, avg_price=0.52)
        driver.push(0.52)
        driver.push(0.52)

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        archived = [row for row in positions if row["active"] == 0]
        assert len(archived) == 1, "出场成交后仓位应归档(active=0)"
        rows = ledger_side_rows(ledger, LONG_EXIT, "closed")
        assert len(rows) == 1, "出场单应回写为 closed"
        assert rows[0]["filled_qty"] == 100.0

        entry_ids = book_ids_by_side(exchange, LONG_ENTRY)
        assert len(entry_ids) == 2, "归档后网格应按当前价重开新仓(下一根 K 线)"
        assert exchange.book[entry_ids[1]].price == 0.52
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_stop_loss_forces_exit_over_resting_order(self, ledger: str) -> None:
        """止损(force)可覆盖休息中的限价止盈单: 撤旧 → 结算 → 按量重挂"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange,
            OrderJournal(ledger),
            grid_spacing_rate=0.5,
            enable_order_stop_loss=True,
            order_stop_loss_rate=0.05,
            take_profit_use_limit_order=True,
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.5)

        resting_ids = book_ids_by_side(exchange, LONG_EXIT)
        assert len(resting_ids) == 1, "盘中路径应先挂出休息止盈单 @0.505"
        assert exchange.book[resting_ids[0]].price == pytest.approx(0.505)

        driver.push(0.47)

        canceled = ledger_side_rows(ledger, LONG_EXIT, "canceled")
        assert len(canceled) == 1, "止损强制离场应撤销休息单"
        active_exits = ledger_side_rows(ledger, LONG_EXIT, "open")
        assert len(active_exits) == 1, "撤旧后应按剩余量重挂止损出场单"
        assert active_exits[0]["quantity"] == 100.0, "重挂量应为全部持仓"
        assert active_exits[0]["price"] == 0.47

        exchange.fill(str(active_exits[0]["order_id"]), 100.0, avg_price=0.47)
        driver.push(0.47)
        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert sorted(row["active"] for row in positions) == [0, 1], (
            "止损出场成交后归档; 出场价 0.47 即当根市价, 盘中路径结算归档后同根重开"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_max_order_stop_loss_closes_all_and_pauses(self, ledger: str) -> None:
        """达到仓位上限止损: 全部强平 + 回调 + 暂停; 暂停后仍跟踪出场单"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange,
            OrderJournal(ledger),
            max_order=3,
            enable_max_order_stop_loss=True,
            fixed_rate_take_profit=False,
        )
        stopped: list[int] = []
        strategy.on_stop_loss_order_all = lambda: stopped.append(1)
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.49)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[1], 100.0, avg_price=0.49)

        driver.push(0.48)

        assert stopped == [1], "达到上限止损必须触发回调"
        assert strategy.is_running is False, "paused_after_stop_loss 应暂停策略"
        exit_ids = book_ids_by_side(exchange, LONG_EXIT)
        assert len(exit_ids) == 2, "两个仓位各挂一张独立出场单"

        exchange.fill(exit_ids[0], 100.0, avg_price=0.48)
        exchange.fill(exit_ids[1], 100.0, avg_price=0.48)
        driver.push(0.48)

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert [row["active"] for row in positions] == [0, 0], "暂停后仍归档已平仓位"
        assert len(book_ids_by_side(exchange, LONG_ENTRY)) == 2, (
            "暂停后不得再开新仓"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_manual_close_position_does_not_pause_or_call_stop_loss(self, ledger: str) -> None:
        """手动全平是独立触发源: 强制离场但不触发止损回调、不暂停"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange,
            OrderJournal(ledger),
            max_order=3,
            enable_max_order_stop_loss=False,
            fixed_rate_take_profit=False,
        )
        stopped: list[int] = []
        strategy.on_stop_loss_order_all = lambda: stopped.append(1)
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.49)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[1], 100.0, avg_price=0.49)

        strategy.close_position = True
        driver.push(0.49)

        assert stopped == [], "手动全平不得触发止损回调"
        assert strategy.is_running is True, "手动全平不得暂停策略"
        assert strategy.close_position is False, "close_position 用后应复位"
        exit_ids = book_ids_by_side(exchange, LONG_EXIT)
        assert len(exit_ids) == 2, "全部仓位强制离场"

        exchange.fill(exit_ids[0], 100.0, avg_price=0.49)
        exchange.fill(exit_ids[1], 100.0, avg_price=0.49)
        driver.push(0.49)
        driver.push(0.49)
        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert sorted(row["active"] for row in positions) == [0, 0, 1]
        assert len(book_ids_by_side(exchange, LONG_ENTRY)) == 3, (
            "未暂停 → 平仓完成后按格重开"
        )

    def test_intraday_resting_take_profit_settles_on_touch(self, ledger: str) -> None:
        """盘中路径: K 线触及休息止盈价即结算归档(收盘价从未达到止盈档)"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange,
            OrderJournal(ledger),
            grid_spacing_rate=0.5,
            take_profit_use_limit_order=True,
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.5)
        resting_id = book_ids_by_side(exchange, LONG_EXIT)[0]
        assert exchange.book[resting_id].price == pytest.approx(0.505)

        exchange.fill(resting_id, 100.0, avg_price=0.505)
        driver.push(0.5, high=0.505, low=0.5)

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        archived = [row for row in positions if row["active"] == 0]
        assert len(archived) == 1, "触及止盈价后应盘中归档"
        exits = ledger_side_rows(ledger, LONG_EXIT)
        assert len(exits) == 1 and exits[0]["status"] == "closed", (
            "唯一出场单来自盘中休息单, 收盘路径不应再挂出场单"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_full_grid_lifecycle_over_many_klines(self, ledger: str) -> None:
        """旗舰用例: 开 → 加 → 双仓止盈 → 归档 → 重开, 每步账本 ⇄ 簿一致"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange, OrderJournal(ledger), fixed_take_profit_rate=0.02
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

        driver.push(0.495)
        assert len(read_positions(ledger, DEFAULT_STRATEGY_KEY)) == 2, "间距达标加仓"
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[1], 100.0, avg_price=0.495)
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

        driver.push(0.502)
        assert book_ids_by_side(exchange, LONG_EXIT) == [], (
            "未达止盈档不得提前离场"
        )

        driver.push(0.512)
        exit_ids = book_ids_by_side(exchange, LONG_EXIT)
        assert len(exit_ids) == 2, "两仓同时达到止盈档, 各挂一张出场单"

        exchange.fill(exit_ids[0], 100.0, avg_price=0.512)
        exchange.fill(exit_ids[1], 100.0, avg_price=0.512)
        driver.push(0.512)
        driver.push(0.512)

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert sorted(row["active"] for row in positions) == [0, 0, 1], (
            "两仓归档 + 网格重开一仓(重开在归档后的下一根 K 线)"
        )
        assert len(read_orders(ledger, DEFAULT_STRATEGY_KEY)) == 5, (
            "2 入场 + 2 出场 + 1 重开入场 = 5 行流水"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)


# ------------------------------------------------------------------ B. 账本不变量


class TestLedgerInvariants:
    """数据库正确性: 对真实 sqlite 文件断言不变量"""

    def test_quantity_matches_order_derivation(self, ledger: str) -> None:
        """数量不变量: quantity = opening + Σ入场成交 − Σ出场成交(逐仓位核账)"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange, OrderJournal(ledger), grid_spacing_rate=0.5
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        entry_id = book_ids_by_side(exchange, LONG_ENTRY)[0]
        exchange.fill(entry_id, 100.0, avg_price=0.5)
        driver.push(0.52)
        exit_id = book_ids_by_side(exchange, LONG_EXIT)[0]
        exchange.fill(exit_id, 100.0, avg_price=0.52)
        driver.push(0.52)
        driver.push(0.52)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[1], 100.0, avg_price=0.52)
        driver.push(0.54)

        orders = read_orders(ledger, DEFAULT_STRATEGY_KEY)
        by_position: dict[str, list[dict[str, Any]]] = {}
        for row in orders:
            by_position.setdefault(str(row["grid_position_id"]), []).append(row)

        for position in read_positions(ledger, DEFAULT_STRATEGY_KEY):
            position_id = str(position["id"])
            derived = float(position["opening_quantity"]) + sum(
                float(row["filled_qty"])
                for row in by_position.get(position_id, [])
                if row["side"] == LONG_ENTRY.value
            ) - sum(
                float(row["filled_qty"])
                for row in by_position.get(position_id, [])
                if row["side"] == LONG_EXIT.value
            )
            expected = 0.0 if position["active"] == 0 else 100.0
            assert derived == expected, (
                f"仓位 {position_id} 派生数量应为 {expected}, 实算 {derived}"
            )

    def test_archived_positions_keep_rows(self, ledger: str) -> None:
        """归档不删行: active=0 且订单流水完整保留"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange, OrderJournal(ledger), grid_spacing_rate=0.5
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        driver.push(0.52)
        exchange.fill(book_ids_by_side(exchange, LONG_EXIT)[0], 100.0, avg_price=0.52)
        before = len(read_orders(ledger, DEFAULT_STRATEGY_KEY))

        driver.push(0.52)

        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1, "归档仓位行必须保留"
        assert positions[0]["active"] == 0
        assert len(read_orders(ledger, DEFAULT_STRATEGY_KEY)) == before, (
            "归档不得删除订单流水"
        )

    def test_created_at_immutable_across_updates(self, ledger: str) -> None:
        """upsert 只更新业务列: created_at 首写不变, updated_at 刷新"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange,
            OrderJournal(ledger),
            grid_spacing_rate=0.5,
            enable_trailing_stop=True,
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(book_ids_by_side(exchange, LONG_ENTRY)[0], 100.0, avg_price=0.5)
        before = read_positions(ledger, DEFAULT_STRATEGY_KEY)[0]

        driver.push(0.52, high=0.52)

        after = read_positions(ledger, DEFAULT_STRATEGY_KEY)[0]
        assert after["trailing_stop_price"] == pytest.approx(0.52 * 0.98), (
            "跟踪止损价应随极值上移并落库"
        )
        assert after["created_at"] == before["created_at"], "created_at 首写不可变"
        assert after["updated_at"] > before["updated_at"], "updated_at 必须刷新"

    def test_lost_response_leaves_exactly_one_pending_row(self, ledger: str) -> None:
        """响应丢失 → 恰好一条 status 为空的行(= 待对账清单), 仓位行已同事务落库"""
        exchange = StatefulFakeExchange()
        exchange.lose_response_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(Exception, match="响应丢失"):
            driver.push(0.5)

        pending = read_pending_orders(ledger, DEFAULT_STRATEGY_KEY)
        assert len(pending) == 1, f"应恰好一条待对账行, got {len(pending)}"
        assert str(pending[0]["order_id"]) in exchange.book, "丢失响应的单已在簿内"
        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1 and positions[0]["active"] == 1, (
            "仓位行必须与预写行同事务落库, 供重启恢复"
        )

    def test_two_strategy_keys_share_one_db_without_leakage(self, ledger: str) -> None:
        """多策略共享一个库: 读写/对账按 strategy_key 严格隔离"""
        exchange = StatefulFakeExchange()
        journal = OrderJournal(ledger)
        long_key = "integ_long_1m"
        short_key = "integ_short_1m"
        long_strategy = make_strategy(exchange, journal, strategy_key=long_key)
        short_strategy = make_strategy(
            exchange,
            journal,
            strategy_key=short_key,
            position_side=PositionSide.SHORT,
            master_side=OrderSide.SELL,
        )

        KlineDriver(long_strategy).push(0.5)
        KlineDriver(short_strategy).push(0.5)

        assert [row["side"] for row in read_orders(ledger, long_key)] == ["buy"]
        assert [row["side"] for row in read_orders(ledger, short_key)] == ["sell"]
        assert len(read_positions(ledger, long_key)) == 1
        assert len(read_positions(ledger, short_key)) == 1

        restarted = make_strategy(exchange, journal, strategy_key=long_key)
        assert len(restarted.positions) == 1, "重启只加载自己键的仓位"
        assert_ledger_matches_exchange(ledger, exchange, [long_key, short_key])
