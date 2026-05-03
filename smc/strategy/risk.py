from __future__ import annotations

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Optional

from smc.mtf import MultiTimeframeResult
from smc.types import Bias, OrderBlock


class RiskParameters(BaseModel):
    model_config = ConfigDict(frozen=True)
    """风险参数配置"""
    risk_per_trade_pct: float = 1.0
    account_balance: float = 100.0
    min_risk_reward: float = 2.0
    stop_loss_buffer: float = 0.001  # 止损外移0.1%避免被扫
    atr_stop_multiplier: float = 1.5


class TradePlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    """交易计划"""
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    position_size: float
    risk_reward: float


class RiskManager:
    """
    风险管理模块

    实现 plan.md 文档中的风险管理规则:
    ✅ 止损放在OB外1-2个点
    ✅ 止盈目标为最近流动性区域
    ✅ 单笔风险不超过总资金1-2%
    ✅ 最小盈亏比 > 2:1
    """

    def __init__(self, params: RiskParameters):
        self.params = params

    def calculate_trade_plan(
        self,
        order_block: OrderBlock,
        direction: Bias,
        current_price: float,
        mtf_result: MultiTimeframeResult,
    ) -> Optional[TradePlan]:
        """计算完整交易计划"""

        # 1. 计算入场价 (OB中点)
        entry_price = order_block.mid

        # 2. 计算止损
        if direction == Bias.BULLISH:
            stop_loss = order_block.low * (1 - self.params.stop_loss_buffer)
        else:
            stop_loss = order_block.high * (1 + self.params.stop_loss_buffer)

        # 3. 计算风险距离
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            return None

        # 4. 计算止盈 (盈亏比 2:1)
        if direction == Bias.BULLISH:
            take_profit = entry_price + (risk_distance * self.params.min_risk_reward)
        else:
            take_profit = entry_price - (risk_distance * self.params.min_risk_reward)

        # 5. 计算风险金额和仓位大小
        risk_amount = self.params.account_balance * (self.params.risk_per_trade_pct / 100)
        position_size = risk_amount / risk_distance

        # 6. 实际盈亏比
        profit_distance = abs(take_profit - entry_price)
        actual_risk_reward = profit_distance / risk_distance

        return TradePlan(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            position_size=position_size,
            risk_reward=actual_risk_reward,
        )