"""仓位对账组件: 周期性核对账本预期持仓与交易所实际持仓, 检测漂移并告警。

设计约束:
- 只读对账, 不自动修复: 漂移原因 (孤儿成交/部分成交/强平/手工平仓) 需人工决策
- 同一交易对可能运行多个策略实例 (如多空反转对), 对账按仓位方向隔离,
  每个策略只核对自己方向的实际持仓
"""

import math
import time
from collections.abc import Callable
from typing import Any

from smart_trader import log
from smart_trader.client.ex_client import ExSwapClient
from smart_trader.model import PositionSide, Symbol

logger = log.getLogger(__name__)


class PositionReconciler:
    """仓位对账器: 按最小间隔节流, 失败只告警不抛出"""

    def __init__(
        self,
        ex_client: ExSwapClient,
        symbol: Symbol,
        position_side: PositionSide,
        interval_sec: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        @param ex_client 交易所客户端
        @param symbol 交易对
        @param position_side 本策略在交易所的仓位方向 (反转策略需先取反)
        @param interval_sec 对账最小间隔秒数
        @param clock 时钟来源, 测试注入假时钟
        """
        self.ex_client = ex_client
        self.symbol = symbol
        self.position_side = position_side
        self.interval_sec = interval_sec
        self.clock = clock
        self._last_run: float | None = None

    def reconcile(self, expected_qty: float) -> None:
        """核对预期持仓与交易所实际持仓; 间隔不足时跳过"""
        now = self.clock()
        if self._last_run is not None and now - self._last_run < self.interval_sec:
            return
        self._last_run = now

        try:
            positions: list[dict[str, Any]] = self.ex_client.positions(
                self.symbol.ccxt()
            )
            actual_qty = self._position_qty(positions)
        except Exception:
            logger.exception("对账获取交易所持仓失败: %s", self.symbol.ccxt())
            return

        if math.isclose(expected_qty, actual_qty, rel_tol=1e-9, abs_tol=1e-9):
            logger.info(
                "仓位对账一致: %s %s %s",
                self.symbol.ccxt(),
                self.position_side.value,
                actual_qty,
            )
            return

        logger.error(
            "仓位对账不一致: %s %s 账本预期 %s, 交易所实际 %s; "
            "可能存在孤儿订单成交/部分成交/强平/手工平仓, 请人工核对",
            self.symbol.ccxt(),
            self.position_side.value,
            expected_qty,
            actual_qty,
        )

    def _position_qty(self, positions: list[dict[str, Any]]) -> float:
        """提取本策略仓位方向的实际持仓数量 (按交易对 + 方向过滤)"""
        total = 0.0
        for position in positions:
            if position.get("symbol") != self.symbol.ccxt():
                continue
            if position.get("side") != self.position_side.value:
                continue
            contracts = position.get("contracts")
            if contracts:
                total += float(contracts)
        return total
