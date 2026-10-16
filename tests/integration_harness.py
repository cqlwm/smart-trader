"""集成测试装配台

把真实装配链(SignalGridStrategy + GridOrderGateway + JournaledTradingPort +
OrderReconciler)接到真实 sqlite 文件账本与状态化交易所上:

- `make_config` / `make_strategy`: 与生产同构的默认配置(LONG 网格, NORMAL 下单,
  固定比例止盈), 全部用例可覆写;
- `KlineDriver`: 确定性 K 线驱动(时间戳自增, 走 `strategy.run()` 公开入口,
  与生产 StrategyTask 同一路径);
- `restart`: 模拟进程重启 —— 同账本文件 + 同交易所实例重建策略,
  走真实重启路径(`__init__` 内对账 + `load_positions`), 无测试专用旁路;
- `assert_ledger_matches_exchange`: 账本 ⇄ 交易所 双向对账断言(最终一致性检验):
  账本无 `status` 为空的行; 簿内每笔订单在账本有同 id 行且事实字段一致;
- 账本裸读助手(sqlite3 独立连接): 证明"另一个进程看到的磁盘状态"。
"""

import sqlite3
from pathlib import Path
from typing import Any, cast

from smart_trader.model import Kline, OrderStatus, PlaceOrderBehavior
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fake_exchange import StatefulFakeExchange
from tests.fakes import DEFAULT_SYMBOL, FakeMarketDataPort

TIMEFRAME = "1m"
TIMESTAMP = 1_700_000_000_000
DEFAULT_STRATEGY_KEY = "integration_long_buy_1m"


def make_config(**overrides: object) -> SignalGridStrategyConfig:
    """集成测试默认配置: LONG 网格 + 固定止盈 + NORMAL 下单(避开追单分支)"""
    fields: dict[str, object] = {
        "symbol": DEFAULT_SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy_key": DEFAULT_STRATEGY_KEY,
        "per_order_qty": 100.0,
        "grid_spacing_rate": 0.01,
        "fixed_rate_take_profit": True,
        "fixed_take_profit_rate": 0.01,
        "place_order_behavior": PlaceOrderBehavior.NORMAL,
        "max_order": 5,
    }
    fields.update(overrides)
    return SignalGridStrategyConfig.model_validate(fields)


def make_strategy(
    exchange: StatefulFakeExchange,
    journal: OrderJournal,
    **overrides: object,
) -> SignalGridStrategy:
    """组装真实装配链的集成策略(对账最小年龄置 0, 避免测试 sleep)"""
    strategy = SignalGridStrategy(
        make_config(**overrides),
        trading=exchange,
        market=FakeMarketDataPort(),
        journal=journal,
        account=exchange,
        reconcile_min_age_seconds=0.0,
    )
    return strategy


def restart(strategy: SignalGridStrategy) -> SignalGridStrategy:
    """模拟进程重启: 同账本 + 同交易所, 重建策略实例

    真实重启路径 = 构造函数内的 `reconciler.reconcile()` + `load_positions()`,
    交易所状态(订单簿)跨实例存活 —— 进程死了, 交易所还活着。
    """
    return SignalGridStrategy(
        strategy.config,
        strategy.trading,
        strategy.market,
        strategy.journal,
        account=cast(StatefulFakeExchange, strategy.trading),
        reconcile_min_age_seconds=0.0,
    )


class KlineDriver:
    """确定性 K 线驱动: 时间戳自增, 每根 K 线走 `strategy.run()`"""

    def __init__(self, strategy: SignalGridStrategy) -> None:
        self._strategy = strategy
        self._timestamp = TIMESTAMP

    def push(
        self,
        close: float,
        *,
        high: float | None = None,
        low: float | None = None,
        finished: bool = True,
    ) -> None:
        """推进一根 K 线; open=close, high/low 缺省与 close 相同"""
        self._strategy.run(
            Kline(
                symbol=self._strategy.config.symbol,
                timeframe=self._strategy.config.timeframe,
                open=close,
                high=close if high is None else high,
                low=close if low is None else low,
                close=close,
                volume=0.0,
                timestamp=self._timestamp,
                finished=finished,
            )
        )
        self._timestamp += 60_000


# ---- 账本裸读(独立 sqlite3 连接 = 另一个进程的视角) ----


def _fetch(
    db_path: str | Path, sql: str, params: tuple[object, ...] = ()
) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def read_orders(
    db_path: str | Path, strategy_key: str | None = None
) -> list[dict[str, Any]]:
    """读取订单流水(含已归档仓位的订单); strategy_key 为 None 时读全部"""
    if strategy_key is None:
        return _fetch(db_path, "SELECT * FROM orders ORDER BY rowid")
    return _fetch(
        db_path,
        "SELECT * FROM orders WHERE strategy_key = ? ORDER BY rowid",
        (strategy_key,),
    )


def read_positions(
    db_path: str | Path, strategy_key: str | None = None
) -> list[dict[str, Any]]:
    """读取全部仓位行(含 active=0 的归档行)"""
    if strategy_key is None:
        return _fetch(db_path, "SELECT * FROM grid_positions ORDER BY rowid")
    return _fetch(
        db_path,
        "SELECT * FROM grid_positions WHERE strategy_key = ? ORDER BY rowid",
        (strategy_key,),
    )


def read_pending_orders(
    db_path: str | Path, strategy_key: str | None = None
) -> list[dict[str, Any]]:
    """读取 status 为空(结果未确认)的订单行, 即待对账清单"""
    if strategy_key is None:
        return _fetch(db_path, "SELECT * FROM orders WHERE status IS NULL ORDER BY rowid")
    return _fetch(
        db_path,
        "SELECT * FROM orders WHERE status IS NULL AND strategy_key = ? ORDER BY rowid",
        (strategy_key,),
    )


def touch_position_created_at(db_path: str | Path, position_id: str, iso: str) -> None:
    """把仓位行的 created_at 改到过去(孤儿超时回收的计时手术)"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE grid_positions SET created_at = ? WHERE id = ?",
            (iso, position_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---- 账本 ⇄ 交易所 双向对账断言 ----


def _same_number(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(cast(float, left) - cast(float, right)) < 1e-9


def assert_ledger_matches_exchange(
    db_path: str | Path,
    exchange: StatefulFakeExchange,
    strategy_keys: str | list[str],
) -> None:
    """账本 ⇄ 交易所 双向对账(最终一致性的直接检验)

    - 账本(限定 strategy_keys)不存在 status 为空的行(调用时机在结算完成后);
    - order_id 唯一;
    - 簿内每笔订单在账本有同 id 行, 委托事实(side/quantity/price)始终一致;
    - 执行事实(status/filled_qty/avg_price)仅在该单已被策略查询过时比对 ——
      策略对成交是惰性感知, 账本保存的是最近一次查询时的事实(设计行为)。
    """
    keys = [strategy_keys] if isinstance(strategy_keys, str) else list(strategy_keys)
    rows: list[dict[str, Any]] = []
    for key in keys:
        rows.extend(read_orders(db_path, key))

    ids = [str(row["order_id"]) for row in rows]
    assert len(ids) == len(set(ids)), f"账本 order_id 必须唯一, got {sorted(ids)}"

    pending = [str(row["order_id"]) for row in rows if row["status"] is None]
    assert not pending, f"结算完成后不得存在待对账行(status 为空): {pending}"

    by_id = {str(row["order_id"]): row for row in rows}
    for custom_id, entry in exchange.book.items():
        if custom_id not in by_id:
            continue  # 多策略共享一个交易所时, 只校验本次声明的键
        row = by_id[custom_id]
        assert str(row["side"]) == entry.side.value, (
            f"订单 {custom_id} 方向不一致: 账本 {row['side']} vs 簿 {entry.side.value}"
        )
        assert _same_number(row["quantity"], entry.quantity), (
            f"订单 {custom_id} 委托量不一致: 账本 {row['quantity']} vs 簿 {entry.quantity}"
        )
        assert _same_number(row["price"], entry.price), (
            f"订单 {custom_id} 委托价不一致: 账本 {row['price']} vs 簿 {entry.price}"
        )
        if not entry.observed:
            continue  # 簿内新事实尚未被策略查询, 执行事实暂不比对
        assert OrderStatus(str(row["status"])) is entry.status, (
            f"订单 {custom_id} 状态不一致: 账本 {row['status']} vs 簿 {entry.status.value}"
        )
        assert _same_number(row["filled_qty"], entry.filled_qty), (
            f"订单 {custom_id} 成交量不一致: 账本 {row['filled_qty']} vs 簿 {entry.filled_qty}"
        )
        assert _same_number(row["avg_price"], entry.avg_price), (
            f"订单 {custom_id} 成交均价不一致: 账本 {row['avg_price']} vs 簿 {entry.avg_price}"
        )
