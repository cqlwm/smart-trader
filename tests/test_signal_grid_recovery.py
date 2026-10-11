"""SignalGridStrategy 集成测试: 意外中断恢复

崩溃点逐个注入(映射 JournaledTradingPort / OrderReconciler 的分支),
"重启" = 同账本文件 + 同交易所实例重建策略 —— 走真实重启路径
(`__init__` 内对账 + `load_positions`), 交易所状态跨实例存活。
"""

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import OrderResult, OrderSide, OrderStatus, Symbol
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
    restart,
    touch_position_created_at,
)

LONG_ENTRY = OrderSide.BUY
LONG_EXIT = OrderSide.SELL


def entry_ids(exchange: StatefulFakeExchange) -> list[str]:
    return [key for key, e in exchange.book.items() if e.side is LONG_ENTRY]


def exit_ids(exchange: StatefulFakeExchange) -> list[str]:
    return [key for key, e in exchange.book.items() if e.side is LONG_EXIT]


@pytest.fixture()
def ledger(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp("recovery") / "smart_trader.db")


class TestCrashRecovery:
    """中断恢复: 崩溃点注入 → 重启 → 对账还原 → 交易继续"""

    def test_restart_settles_lost_response_from_exchange(self, ledger: str) -> None:
        """响应丢失(单已入簿) → 重启对账用交易所事实回写, 仓位还原, 交易继续"""
        exchange = StatefulFakeExchange()
        exchange.lose_response_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(SmartTraderError, match="响应丢失"):
            driver.push(0.5)

        assert len(read_pending_orders(ledger, DEFAULT_STRATEGY_KEY)) == 1
        restarted = restart(strategy)
        assert len(entry_ids(exchange)) == 1, "交易所簿不受重启影响"

        assert read_pending_orders(ledger, DEFAULT_STRATEGY_KEY) == [], (
            "对账必须用交易所事实回写待对账行"
        )
        rows = read_orders(ledger, DEFAULT_STRATEGY_KEY)
        assert rows[0]["status"] == "open", "恢复后的入场单状态应与簿一致(open)"
        assert len(restarted.positions) == 1, "仓位必须从账本还原"
        assert len(restarted.positions[0].pending_entry_orders) == 1

        # 还原后交易继续: 入场成交 → 止盈离场
        exchange.fill(entry_ids(exchange)[0], 100.0, avg_price=0.5)
        driver = KlineDriver(restarted)
        driver.push(0.52)
        assert len(exit_ids(exchange)) == 1, "重启后止盈路径照常工作"
        assert read_orders(ledger, DEFAULT_STRATEGY_KEY)[0]["order_id"] != (
            read_orders(ledger, DEFAULT_STRATEGY_KEY)[1]["order_id"]
        ), "恢复的 id 已作废, 新单必须换新 id"
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_restart_discards_row_exchange_never_saw(self, ledger: str) -> None:
        """请求未到达交易所 → 重启对账删除预写行, 无幽灵订单, 网格可继续"""
        exchange = StatefulFakeExchange()
        exchange.drop_request_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(SmartTraderError, match="请求未到达"):
            driver.push(0.5)

        assert exchange.book == {}, "交易所从未见过该订单"
        restarted = restart(strategy)

        assert read_pending_orders(ledger, DEFAULT_STRATEGY_KEY) == []
        assert read_orders(ledger, DEFAULT_STRATEGY_KEY) == [], "查无此单必须删行"
        assert len(restarted.positions) == 1, "空仓位保留作网格价格参考"
        assert restarted.positions[0].orders == []

        driver = KlineDriver(restarted)
        driver.push(0.49)
        assert len(entry_ids(exchange)) == 1, "删行后网格正常重新入场(无幽灵单阻塞)"
        assert len(read_orders(ledger, DEFAULT_STRATEGY_KEY)) == 1
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_restart_after_partial_entry_fill_exits_actual_quantity(
        self, ledger: str
    ) -> None:
        """回归: 入场单部分成交后中断 → 对账恢复成交量 → 出场按实际量 30 而非 100"""
        exchange = StatefulFakeExchange()
        exchange.lose_response_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(SmartTraderError, match="响应丢失"):
            driver.push(0.5)
        exchange.fill(entry_ids(exchange)[0], 30.0, avg_price=0.5, status=OrderStatus.OPEN)

        restarted = restart(strategy)
        assert restarted.positions[0].quantity == 30.0, (
            "对账必须恢复已成交量, 仓位派生量应为 30"
        )

        driver = KlineDriver(restarted)
        driver.push(0.52)

        exits = [
            row
            for row in read_orders(ledger, DEFAULT_STRATEGY_KEY)
            if row["side"] == LONG_EXIT.value
        ]
        assert len(exits) == 1, "止盈应挂一张出场单"
        assert exits[0]["quantity"] == 30.0, (
            f"出场量必须是实际持仓 30, 不得按委托量 100 超挂/丢敞口, got {exits[0]['quantity']}"
        )
        entry_row = read_orders(ledger, DEFAULT_STRATEGY_KEY)[0]
        assert entry_row["status"] == "canceled", "剩余 70 的入场单应被撤掉"
        assert entry_row["filled_qty"] == 30.0, "撤单必须保留已成交量"
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_restart_restores_pending_exit_and_does_not_duplicate(
        self, ledger: str
    ) -> None:
        """出场单挂着时重启 → 还原 EXIT_PENDING, 不重复挂单, 成交后照常归档"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(entry_ids(exchange)[0], 100.0, avg_price=0.5)
        driver.push(0.52)
        assert len(exit_ids(exchange)) == 1

        restarted = restart(strategy)
        assert restarted.positions[0].has_pending_exit, "出场单必须从账本还原"

        driver = KlineDriver(restarted)
        driver.push(0.52)
        assert len(exchange.book) == 2, "价格未变 → 保留原挂单, 不得重复下单"
        assert len(read_orders(ledger, DEFAULT_STRATEGY_KEY)) == 2

        exchange.fill(exit_ids(exchange)[0], 100.0, avg_price=0.52)
        driver.push(0.52)
        positions = read_positions(ledger, DEFAULT_STRATEGY_KEY)
        assert [row["active"] for row in positions] == [0], "出场成交后归档"
        assert len(read_orders(ledger, DEFAULT_STRATEGY_KEY)) == 2, (
            "自始至终只有入场 + 出场两张订单"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_restart_restores_filled_position_without_duplicate_entry(
        self, ledger: str
    ) -> None:
        """已入场仓位重启 → 还原后不重复开仓; 价格上行时止盈量正确(=100)"""
        exchange = StatefulFakeExchange()
        strategy = make_strategy(
            exchange, OrderJournal(ledger), grid_spacing_rate=0.5
        )
        driver = KlineDriver(strategy)

        driver.push(0.5)
        exchange.fill(entry_ids(exchange)[0], 100.0, avg_price=0.5)

        restarted = restart(strategy)
        assert len(restarted.positions) == 1

        driver = KlineDriver(restarted)
        driver.push(0.5)
        assert len(entry_ids(exchange)) == 1, "价格未走出间距 → 不得重复开仓"

        driver.push(0.52)
        exits = [
            row
            for row in read_orders(ledger, DEFAULT_STRATEGY_KEY)
            if row["side"] == LONG_EXIT.value
        ]
        assert len(exits) == 1 and exits[0]["quantity"] == 100.0, (
            "止盈离场必须基于还原后的持仓派生量"
        )
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_reconcile_tolerates_query_failure_and_retries_next_start(
        self, ledger: str
    ) -> None:
        """对账查询失败 → 保留待对账行, 下次启动重试成功(单条容错不中断)"""
        exchange = FlakyQueryExchange()
        exchange.lose_response_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(SmartTraderError, match="响应丢失"):
            driver.push(0.5)

        exchange.fail_query_next = True
        first = restart(strategy)
        pending = read_pending_orders(ledger, DEFAULT_STRATEGY_KEY)
        assert len(pending) == 1, "查询失败必须保留待对账行, 留给下次启动"

        second = restart(first)
        assert read_pending_orders(ledger, DEFAULT_STRATEGY_KEY) == [], (
            "下次启动对账必须成功回写"
        )
        assert read_orders(ledger, DEFAULT_STRATEGY_KEY)[0]["status"] == "open"
        assert len(second.positions) == 1
        assert_ledger_matches_exchange(ledger, exchange, DEFAULT_STRATEGY_KEY)

    def test_orphan_empty_position_reaped_after_timeout(self, ledger: str) -> None:
        """孤儿回收: 无订单的空仓位超过阈值(超时计时手术) → 下一根 K 线回收"""
        exchange = StatefulFakeExchange()
        exchange.drop_request_next = True
        strategy = make_strategy(exchange, OrderJournal(ledger))
        driver = KlineDriver(strategy)

        with pytest.raises(SmartTraderError, match="请求未到达"):
            driver.push(0.5)

        # 计时手术必须在重启前做: 重启时 load_positions 读到的才是改过的 created_at
        position_id = str(read_positions(ledger, DEFAULT_STRATEGY_KEY)[0]["id"])
        touch_position_created_at(ledger, position_id, "2026-01-01T00:00:00+00:00")

        restarted = restart(strategy)

        driver = KlineDriver(restarted)
        driver.push(0.5)

        row = read_positions(ledger, DEFAULT_STRATEGY_KEY)[0]
        assert row["active"] == 0, "超时空仓位必须被回收(active=0)"
        assert restarted.positions == [], "回收后从内存登记表移除"
        assert read_orders(ledger, DEFAULT_STRATEGY_KEY) == []


class FlakyQueryExchange(StatefulFakeExchange):
    """对账查询可注入一次性失败的交易所"""

    def __init__(self) -> None:
        super().__init__()
        self.fail_query_next = False

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        if self.fail_query_next:
            self.fail_query_next = False
            raise SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "对账查询失败")
        return super().query(custom_id, symbol)
