"""SIGKILL 档崩溃恢复(标记 @pytest.mark.integration, 默认不参与常规跑)

用真实子进程在"预写完成、结果未知"的中断点以 `os._exit(9)` 死亡 ——
不清理、不 checkpoint WAL —— 验证 OS 级持久性:

1. 独立 sqlite3 连接(另一个进程视角)读到 `status IS NULL` 的预写行与活跃仓位行;
2. 交易所事实经 JSON 持久化簿存活(进程死了, 交易所还活着);
3. 重启(真实路径: 对账 + load_positions)后账本与交易所事实一致。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from smart_trader.storage.order_journal import OrderJournal
from tests.fake_exchange import StatefulFakeExchange
from tests.integration_harness import (
    DEFAULT_STRATEGY_KEY,
    assert_ledger_matches_exchange,
    make_strategy,
    read_orders,
    read_pending_orders,
    read_positions,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

WORKER_SOURCE = '''"""崩溃 worker: 在下单路径中断点以 os._exit(9) 模拟进程死亡"""
import os
import sys

from smart_trader.exceptions import SmartTraderError
from smart_trader.storage.order_journal import OrderJournal
from tests.fake_exchange import StatefulFakeExchange
from tests.integration_harness import KlineDriver, make_strategy


def main() -> None:
    db_path, book_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    exchange = StatefulFakeExchange(persist_path=book_path)
    if mode == "lose_response":
        exchange.lose_response_next = True
    else:
        exchange.drop_request_next = True
    strategy = make_strategy(exchange, OrderJournal(db_path))
    try:
        KlineDriver(strategy).push(0.5)
    except SmartTraderError:
        pass
    os._exit(9)


if __name__ == "__main__":
    main()
'''


def run_worker(db_path: Path, book_path: Path, mode: str, tmp_path: Path) -> None:
    """以独立进程运行崩溃 worker, 断言其以 os._exit(9) 死亡"""
    worker = tmp_path / "crash_worker.py"
    worker.write_text(WORKER_SOURCE)
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    proc = subprocess.run(
        [sys.executable, str(worker), str(db_path), str(book_path), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 9, (
        f"worker 应以 os._exit(9) 退出, got {proc.returncode}\nstderr:\n{proc.stderr}"
    )


@pytest.mark.integration
class TestSigkillDuringPlace:
    """进程死亡于下单路径中段: 磁盘状态可恢复"""

    def test_kill_after_order_reached_exchange_recovers_from_book(
        self, tmp_path: Path
    ) -> None:
        """响应丢失后死亡: 预写行已在盘上, 重启对账从交易所簿回写"""
        db_path = tmp_path / "ledger.db"
        book_path = tmp_path / "book.json"
        run_worker(db_path, book_path, "lose_response", tmp_path)

        pending = read_pending_orders(db_path, DEFAULT_STRATEGY_KEY)
        assert len(pending) == 1, "status 为空的预写行必须已持久化到磁盘"
        positions = read_positions(db_path, DEFAULT_STRATEGY_KEY)
        assert len(positions) == 1 and positions[0]["active"] == 1, (
            "仓位行必须与预写行同事务落库并持久化"
        )

        exchange = StatefulFakeExchange(persist_path=book_path)
        assert len(exchange.book) == 1, "已到达交易所的订单必须活在持久化簿里"

        strategy = make_strategy(exchange, OrderJournal(str(db_path)))
        assert read_pending_orders(db_path, DEFAULT_STRATEGY_KEY) == [], (
            "重启对账必须用交易所事实回写待对账行"
        )
        rows = read_orders(db_path, DEFAULT_STRATEGY_KEY)
        assert rows[0]["status"] == "open", "回写后状态应与簿一致"
        assert len(strategy.positions) == 1, "仓位必须从账本还原"
        assert_ledger_matches_exchange(db_path, exchange, DEFAULT_STRATEGY_KEY)

    def test_kill_before_exchange_accepts_leaves_no_ghost(self, tmp_path: Path) -> None:
        """请求未到达即死亡: 预写行落盘, 重启对账查无删行, 不留幽灵订单"""
        db_path = tmp_path / "ledger.db"
        book_path = tmp_path / "book.json"
        run_worker(db_path, book_path, "drop_request", tmp_path)

        assert len(read_pending_orders(db_path, DEFAULT_STRATEGY_KEY)) == 1, (
            "结果未知的预写行必须已持久化到磁盘"
        )

        exchange = StatefulFakeExchange(persist_path=book_path)
        assert exchange.book == {}, "请求未到达, 交易所簿必须为空"

        strategy = make_strategy(exchange, OrderJournal(str(db_path)))
        assert read_orders(db_path, DEFAULT_STRATEGY_KEY) == [], (
            "对账查无此单必须删除预写行"
        )
        assert len(strategy.positions) == 1, "空仓位保留作网格价格参考"
