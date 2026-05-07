from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict

from model import Symbol
from strategies.smc.mtf import TIMEFRAME_MINUTES, MultiTimeframeAnalyzer, MultiTimeframeResult
from strategies.smc.schemas import (
    ConditionOutput,
    DirectionContextOutput,
    FVGOutput,
    IntradaySignalOutput,
    OBOutput,
    SetupZoneOutput,
    TradePlanOutput,
    TriggerContextOutput,
)
from strategies.smc.strategy.intraday import (
    DirectionalContext,
    IntradaySignal,
    MTFAnalysis,
    SetupZone,
)
from strategies.smc.strategy.risk import RiskManager, RiskParameters, TradePlan
from strategies.smc.types import Bias, OBStatus, OrderBlock

logger = logging.getLogger(__name__)


class SimpleIntradayConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: Symbol = Symbol(base="BTC", quote="USDC")
    risk_per_trade_pct: float = 1.0
    account_balance: float = 100.0
    min_risk_reward: float = 2.0
    swing_length: int = 50

    timeframes: tuple[str, ...] = ("1w", "1d", "5m")
    direction_timeframes: tuple[str, ...] = ("1w", "1d")
    entry_timeframe: str = "5m"
    doji_threshold_pct: float = 0.1
    max_ob_distance_pct: float = 3.0


class SimpleIntradayStrategy:

    def __init__(self, config: Optional[SimpleIntradayConfig] = None):
        self.config = config or SimpleIntradayConfig()
        self.risk_manager = RiskManager(
            RiskParameters(
                risk_per_trade_pct=self.config.risk_per_trade_pct,
                account_balance=self.config.account_balance,
                min_risk_reward=self.config.min_risk_reward,
            )
        )

    def analyze(self) -> IntradaySignal:
        mtf_result = self._run_multi_timeframe_analysis()
        return self.analyze_result(mtf_result)

    def analyze_result(
        self,
        mtf_result: MultiTimeframeResult,
        close_time: datetime | None = None,
    ) -> IntradaySignal:
        analyzed_at = (
            close_time.isoformat()
            if close_time
            else datetime.now(timezone.utc).isoformat()
        )

        if not mtf_result.results:
            return self._create_wait_signal(
                current_price=0.0,
                blocked_reasons=["多周期分析失败，无可用结果"],
                analyzed_at=analyzed_at,
            )

        current_price = self._extract_current_price(mtf_result)
        direction_context = self._determine_direction(mtf_result)

        if direction_context.bias == Bias.NEUTRAL:
            return self._create_wait_signal(
                current_price=current_price,
                blocked_reasons=direction_context.blocked_reasons,
                direction_context=direction_context,
                fail_gate="G1_Direction",
                analyzed_at=analyzed_at,
            )

        setup_zone = self._find_matching_ob(
            mtf_result, direction_context.bias, current_price
        )
        if setup_zone is None:
            return self._create_wait_signal(
                current_price=current_price,
                blocked_reasons=[f"{self.config.entry_timeframe} 未发现同向 OB"],
                direction_context=direction_context,
                fail_gate="G2_Setup",
                analyzed_at=analyzed_at,
            )

        trade_plan = self._calculate_market_trade_plan(
            setup_zone.order_block,
            direction_context.bias,
            current_price,
            mtf_result,
            setup_zone.timeframe,
        )
        if trade_plan is None:
            return self._create_wait_signal(
                current_price=current_price,
                blocked_reasons=["无法计算交易计划"],
                direction_context=direction_context,
                setup_zone=setup_zone,
                fail_gate="G3_Plan",
                analyzed_at=analyzed_at,
            )

        action = "LONG" if direction_context.bias == Bias.BULLISH else "SHORT"
        return IntradaySignal(
            symbol=self.config.symbol,
            action=action,
            confidence=direction_context.confidence,
            bias=direction_context.bias,
            trade_plan=trade_plan,
            conditions=[],
            analyzed_at=analyzed_at,
            current_price=current_price,
            direction_context=direction_context,
            setup_zone=setup_zone,
            trigger_context=None,
            opportunity_score=0,
            blocked_reasons=[],
        )

    def _determine_direction(
        self, result: MultiTimeframeResult
    ) -> DirectionalContext:
        reasons: list[str] = []
        blocked: list[str] = []
        aligned: list[str] = []
        biases: dict[str, Bias] = {}

        for tf in self.config.direction_timeframes:
            smc_result = result.results.get(tf)
            if smc_result is None:
                blocked.append(f"缺少 {tf} 数据")
                continue

            candle_bias = self._candle_bias(smc_result.df)
            biases[tf] = candle_bias

            if candle_bias == Bias.BULLISH:
                reasons.append(f"{tf} 已闭合K线: close > open (看多)")
                aligned.append(tf)
            elif candle_bias == Bias.BEARISH:
                reasons.append(f"{tf} 已闭合K线: close < open (看空)")
                aligned.append(tf)
            else:
                blocked.append(f"{tf} 已闭合K线为十字星")

        non_neutral = [b for b in biases.values() if b != Bias.NEUTRAL]
        all_present = len(biases) == len(self.config.direction_timeframes)

        if all_present and len(non_neutral) == len(biases) and len(set(non_neutral)) == 1:
            bias = non_neutral[0]
            confidence = 80
        elif len(non_neutral) < len(self.config.direction_timeframes):
            bias = Bias.NEUTRAL
            confidence = 0
            if "周K/日K方向不一致" not in blocked:
                blocked.append("部分周期为十字星或缺少数据")
        else:
            bias = Bias.NEUTRAL
            confidence = 0
            blocked.append("周K/日K方向不一致")

        return DirectionalContext(
            bias=bias,
            score=0,
            confidence=confidence,
            reasons=reasons,
            aligned_timeframes=aligned,
            blocked_reasons=blocked,
        )

    def _candle_bias(self, df) -> Bias:
        if len(df) < 2:
            return Bias.NEUTRAL

        last_closed = df.iloc[-2]
        open_price = float(last_closed["open"])
        close_price = float(last_closed["close"])
        mid = (open_price + close_price) / 2

        if mid == 0:
            return Bias.NEUTRAL

        body_pct = abs(close_price - open_price) / mid * 100
        if body_pct < self.config.doji_threshold_pct:
            return Bias.NEUTRAL

        if close_price > open_price:
            return Bias.BULLISH
        if close_price < open_price:
            return Bias.BEARISH
        return Bias.NEUTRAL

    def _find_matching_ob(
        self,
        result: MultiTimeframeResult,
        direction: Bias,
        current_price: float,
    ) -> SetupZone | None:
        smc_result = result.results.get(self.config.entry_timeframe)
        if smc_result is None:
            return None

        all_obs = smc_result.swing_order_blocks + smc_result.internal_order_blocks
        candidates: list[SetupZone] = []

        for ob in all_obs:
            if ob.bias != direction or ob.status == OBStatus.MITIGATED:
                continue

            distance_pct = (
                abs(current_price - ob.mid) / ob.mid * 100 if ob.mid else 0.0
            )
            if distance_pct > self.config.max_ob_distance_pct:
                continue

            source_priority = 2 if ob.source == "swing" else 1
            reasons = (
                ["OB 未测试"]
                if ob.status == OBStatus.UNTESTED
                else ["OB 已测试但未失效"]
            )

            candidates.append(
                SetupZone(
                    timeframe=self.config.entry_timeframe,
                    order_block=ob,
                    overlapping_fvg=None,
                    zone_quality_score=0,
                    zone_position_ok=True,
                    distance_to_price_pct=distance_pct,
                    reasons=reasons,
                    source_priority=source_priority,
                )
            )

        if not candidates:
            return None

        candidates.sort(key=lambda z: (-z.source_priority, z.distance_to_price_pct))
        return candidates[0]

    def _calculate_market_trade_plan(
        self,
        order_block: OrderBlock,
        direction: Bias,
        current_price: float,
        mtf_result: MultiTimeframeResult,
        timeframe: str,
    ) -> TradePlan | None:
        entry_price = current_price
        smc_result = mtf_result.results.get(timeframe)
        atr = smc_result.current_atr if smc_result else 0.0

        if direction == Bias.BULLISH:
            stop_loss = order_block.low - atr
        else:
            stop_loss = order_block.high + atr

        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            return None

        if direction == Bias.BULLISH:
            take_profit = entry_price + risk_distance * 2.0
        else:
            take_profit = entry_price - risk_distance * 2.0

        risk_amount = self.config.account_balance * (
            self.config.risk_per_trade_pct / 100
        )
        position_size = risk_amount / risk_distance

        return TradePlan(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            position_size=position_size,
            risk_reward=2.0,
        )

    def _extract_current_price(self, result: MultiTimeframeResult) -> float:
        available = {
            tf: smc
            for tf, smc in result.results.items()
            if tf in TIMEFRAME_MINUTES
        }
        smallest_tf = min(available, key=lambda tf: TIMEFRAME_MINUTES[tf])
        return float(available[smallest_tf].df["close"].iloc[-1])

    def _create_wait_signal(
        self,
        current_price: float,
        blocked_reasons: list[str],
        direction_context: DirectionalContext | None = None,
        setup_zone: SetupZone | None = None,
        fail_gate: str = "",
        fail_detail: str = "",
        analyzed_at: str = "",
    ) -> IntradaySignal:
        direction_context = direction_context or DirectionalContext(
            bias=Bias.NEUTRAL,
            score=0,
            confidence=0,
            reasons=[],
            aligned_timeframes=[],
            blocked_reasons=blocked_reasons,
        )
        return IntradaySignal(
            symbol=self.config.symbol,
            action="WAIT",
            confidence=direction_context.confidence,
            bias=direction_context.bias,
            trade_plan=None,
            conditions=[],
            analyzed_at=analyzed_at or datetime.now(timezone.utc).isoformat(),
            current_price=current_price,
            direction_context=direction_context,
            setup_zone=setup_zone,
            trigger_context=None,
            opportunity_score=0,
            blocked_reasons=list(dict.fromkeys(blocked_reasons)),
            fail_gate=fail_gate,
            fail_detail=fail_detail,
        )

    def to_json(self, signal: IntradaySignal) -> dict:
        trade_plan = None
        if signal.trade_plan:
            trade_plan = TradePlanOutput(
                entry_price=signal.trade_plan.entry_price,
                stop_loss=signal.trade_plan.stop_loss,
                take_profit=signal.trade_plan.take_profit,
                risk_amount=signal.trade_plan.risk_amount,
                position_size=signal.trade_plan.position_size,
                risk_reward=signal.trade_plan.risk_reward,
            )

        conditions = [
            ConditionOutput(
                name=condition.name,
                passed=condition.passed,
                description=condition.description,
                weight=condition.weight,
                is_hard_filter=condition.is_hard_filter,
            )
            for condition in signal.conditions
        ]

        setup_zone = None
        if signal.setup_zone:
            setup_zone = SetupZoneOutput(
                timeframe=signal.setup_zone.timeframe,
                order_block=OBOutput(
                    id=signal.setup_zone.order_block.id,
                    bias=signal.setup_zone.order_block.bias.name,
                    high=signal.setup_zone.order_block.high,
                    low=signal.setup_zone.order_block.low,
                    mid=signal.setup_zone.order_block.mid,
                    formed_time=signal.setup_zone.order_block.formed_time,
                    status=signal.setup_zone.order_block.status.name,
                    distance_pct=0.0,
                ),
                overlapping_fvg=None
                if signal.setup_zone.overlapping_fvg is None
                else FVGOutput(
                    id=signal.setup_zone.overlapping_fvg.id,
                    bias=signal.setup_zone.overlapping_fvg.bias.name,
                    high=signal.setup_zone.overlapping_fvg.top,
                    low=signal.setup_zone.overlapping_fvg.bottom,
                    mid=signal.setup_zone.overlapping_fvg.mid,
                    formed_time=signal.setup_zone.overlapping_fvg.formed_time,
                    status=signal.setup_zone.overlapping_fvg.status.name,
                    fill_pct=signal.setup_zone.overlapping_fvg.fill_pct,
                    width=signal.setup_zone.overlapping_fvg.width,
                    width_atr_ratio=signal.setup_zone.overlapping_fvg.width_atr_ratio,
                    mitigation_depth=signal.setup_zone.overlapping_fvg.mitigation_depth,
                    touch_count=signal.setup_zone.overlapping_fvg.touch_count,
                ),
                zone_quality_score=signal.setup_zone.zone_quality_score,
                zone_position_ok=signal.setup_zone.zone_position_ok,
                distance_to_price_pct=signal.setup_zone.distance_to_price_pct,
                reasons=signal.setup_zone.reasons,
            )

        trigger_context = None
        if signal.trigger_context:
            trigger_context = TriggerContextOutput(
                timeframe=signal.trigger_context.timeframe,
                structure_aligned=signal.trigger_context.structure_aligned,
                last_trigger_event=signal.trigger_context.last_trigger_event,
                liquidity_swept=signal.trigger_context.liquidity_swept,
                fvg_reaction=signal.trigger_context.fvg_reaction,
                trigger_score=signal.trigger_context.trigger_score,
                reasons=signal.trigger_context.reasons,
            )

        output = IntradaySignalOutput(
            strategy="simple_intraday",
            symbol=signal.symbol,
            action=signal.action,
            confidence=signal.confidence,
            current_price=signal.current_price,
            analyzed_at=signal.analyzed_at,
            trade_plan=trade_plan,
            conditions=conditions,
            direction_context=DirectionContextOutput(
                bias=signal.direction_context.bias.name,
                score=signal.direction_context.score,
                confidence=signal.direction_context.confidence,
                reasons=signal.direction_context.reasons,
                aligned_timeframes=signal.direction_context.aligned_timeframes,
                blocked_reasons=signal.direction_context.blocked_reasons,
            ),
            setup_zone=setup_zone,
            trigger_context=trigger_context,
            opportunity_score=signal.opportunity_score,
            blocked_reasons=signal.blocked_reasons,
        )

        return output.model_dump()

    def _run_multi_timeframe_analysis(self) -> MultiTimeframeResult:
        from strategies.smc.mtf import MultiTimeframeConfig

        mtf_config = MultiTimeframeConfig(
            exchange_id=self.config.exchange_id,
            symbol=self.config.symbol,
            timeframes=self.config.timeframes,
            swing_length=self.config.swing_length,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            account_balance=self.config.account_balance,
        )

        analyzer = MultiTimeframeAnalyzer(mtf_config)
        return analyzer.analyze()

    def build_mtf_analysis(
        self,
        result: MultiTimeframeResult,
        current_price: float | None = None,
    ) -> MTFAnalysis:
        current_price = (
            current_price
            if current_price is not None
            else self._extract_current_price(result)
        )
        direction_context = self._determine_direction(result)
        blocked_reasons = list(direction_context.blocked_reasons)

        setup_zone = None
        if direction_context.bias != Bias.NEUTRAL:
            setup_zone = self._find_matching_ob(
                result, direction_context.bias, current_price
            )
            if setup_zone is None:
                blocked_reasons.append(
                    f"{self.config.entry_timeframe} 未发现同向 OB"
                )

        narrative = self._build_narrative(
            direction_context, setup_zone, blocked_reasons
        )

        return MTFAnalysis(
            direction_context=direction_context,
            setup_zone=setup_zone,
            trigger_context=None,
            best_opportunity=None,
            blocked_reasons=blocked_reasons,
            narrative=narrative,
        )

    def _build_narrative(
        self,
        direction_context: DirectionalContext,
        setup_zone: SetupZone | None,
        blocked_reasons: list[str],
    ) -> str:
        if direction_context.bias == Bias.NEUTRAL:
            return "周K/日K方向不一致，观望。"

        action = "看多" if direction_context.bias == Bias.BULLISH else "看空"
        parts = [f"周K + 日K{action}一致。"]

        if setup_zone is not None:
            parts.append(
                f"{self.config.entry_timeframe} 发现同向 OB。"
            )
        else:
            parts.append(
                f"{self.config.entry_timeframe} 未发现同向 OB。"
            )

        if blocked_reasons:
            parts.append(f"阻碍: {'; '.join(dict.fromkeys(blocked_reasons))}。")

        return " ".join(parts)
