"""PositionReconciler 对账组件测试"""

import logging
from typing import Any
from unittest.mock import Mock

import pytest

from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import PositionSide, Symbol
from smart_trader.strategy.position_reconciler import PositionReconciler

SYMBOL = Symbol(base="DOGE", quote="USDC")
LOGGER_NAME = "smart_trader.strategy.position_reconciler"


class FakeClock:
    """可推进的假时钟"""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_reconciler(
    interval_sec: float = 300.0,
) -> tuple[PositionReconciler, Mock, FakeClock]:
    ex_client = Mock(spec=ExSwapClient)
    ex_client.positions.return_value = []
    clock = FakeClock()
    reconciler = PositionReconciler(
        ex_client=ex_client,
        symbol=SYMBOL,
        position_side=PositionSide.SHORT,
        interval_sec=interval_sec,
        clock=clock,
    )
    return reconciler, ex_client, clock


def position(
    side: str, contracts: float, symbol: str = "DOGE/USDC"
) -> dict[str, Any]:
    return {"symbol": symbol, "side": side, "contracts": contracts}


def error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_matching_position_logs_no_error(caplog: pytest.LogCaptureFixture):
    """账本与交易所一致时不产生告警; 对账只关注自己方向的仓位"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.return_value = [
        position("short", 750.0),
        position("long", 1000.0),  # 反向策略的仓位, 不归本策略管
    ]

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert error_records(caplog) == []
    ex_client.positions.assert_called_once_with("DOGE/USDC")


def test_position_shortfall_logs_error(caplog: pytest.LogCaptureFixture):
    """交易所仓位少于账本预期 (强平/手工平仓): 告警"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.return_value = [position("short", 500.0)]

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert len(error_records(caplog)) == 1
    assert "仓位对账不一致" in caplog.text


def test_position_extra_logs_error(caplog: pytest.LogCaptureFixture):
    """交易所仓位多于账本预期 (孤儿成交): 告警"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.return_value = [position("short", 1000.0)]

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert len(error_records(caplog)) == 1
    assert "仓位对账不一致" in caplog.text


def test_position_absent_on_exchange_logs_error(caplog: pytest.LogCaptureFixture):
    """交易所无该方向仓位但账本有持仓: 告警"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.return_value = [position("long", 100.0)]

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert len(error_records(caplog)) == 1


def test_wrong_symbol_position_ignored(caplog: pytest.LogCaptureFixture):
    """其他交易对的同方向仓位不参与对账 (隔离场景)"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.return_value = [position("short", 750.0, symbol="ETH/USDC")]

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert len(error_records(caplog)) == 1, "他交易对仓位不应被计入本策略实际持仓"


def test_zero_expected_and_empty_positions_matches(caplog: pytest.LogCaptureFixture):
    """空账本 + 空仓位: 一致无告警 (空值场景)"""
    reconciler, _, _ = build_reconciler()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        reconciler.reconcile(0.0)

    assert error_records(caplog) == []


def test_reconcile_throttled_within_interval():
    """间隔内重复调用被节流"""
    reconciler, ex_client, clock = build_reconciler(interval_sec=300.0)

    reconciler.reconcile(750.0)
    clock.advance(100.0)
    reconciler.reconcile(750.0)

    assert ex_client.positions.call_count == 1


def test_reconcile_runs_again_after_interval():
    """超过间隔后再次对账"""
    reconciler, ex_client, clock = build_reconciler(interval_sec=300.0)

    reconciler.reconcile(750.0)
    clock.advance(300.0)
    reconciler.reconcile(750.0)

    assert ex_client.positions.call_count == 2


def test_reconcile_fetch_failure_logs_and_survives(
    caplog: pytest.LogCaptureFixture,
):
    """获取仓位失败: 只告警不抛出, 不影响交易主流程"""
    reconciler, ex_client, _ = build_reconciler()
    ex_client.positions.side_effect = RuntimeError("network")

    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        reconciler.reconcile(750.0)

    assert len(error_records(caplog)) == 1
