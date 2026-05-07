from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict

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
from strategies.smc.strategy.conditions import EntryConditionChecker, EntryConditionResult
from strategies.smc.strategy.risk import RiskManager, RiskParameters, TradePlan
from strategies.smc.types import Bias, EventType, FairValueGap, FVGStatus, OBStatus, OrderBlock, ZonePosition

logger = logging.getLogger(__name__)


class DirectionalContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    bias: Bias
    score: int
    confidence: int
    reasons: list[str]
    aligned_timeframes: list[str]
    blocked_reasons: list[str]


class SetupZone(BaseModel):
    model_config = ConfigDict(frozen=True)
    timeframe: str
    order_block: OrderBlock
    overlapping_fvg: FairValueGap | None
    zone_quality_score: int
    zone_position_ok: bool
    distance_to_price_pct: float
    reasons: list[str]
    source_priority: int


class TriggerContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    timeframe: str
    structure_aligned: bool
    last_trigger_event: str | None
    liquidity_swept: bool
    fvg_reaction: bool
    trigger_score: int
    reasons: list[str]


class EntryOpportunity(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction: Bias
    setup_zone: SetupZone
    trigger: TriggerContext
    total_score: int
    confidence: int
    passed_hard_filters: bool
    failed_filters: list[str]


class IntradaySignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    """日内交易策略信号"""

    symbol: str
    action: str  # LONG, SHORT, WAIT
    confidence: int  # 0-100
    bias: Bias
    trade_plan: Optional[TradePlan]
    conditions: list[EntryConditionResult]
    analyzed_at: str
    current_price: float
    direction_context: DirectionalContext
    setup_zone: SetupZone | None
    trigger_context: TriggerContext | None
    opportunity_score: int
    blocked_reasons: list[str]
    fail_gate: str | None = None
    fail_detail: str = ""

    @property
    def is_valid(self) -> bool:
        return self.action in ("LONG", "SHORT") and self.trade_plan is not None


class IntradayConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    """日内策略配置"""

    exchange_id: str = "binanceusdm"
    symbol: str = "BTC/USDT:USDT"
    risk_per_trade_pct: float = 1.0
    account_balance: float = 100.0
    min_risk_reward: float = 2.0
    swing_length: int = 50

    # 时间框架配置 (大周期 -> 入场周期)
    timeframes: tuple[str, ...] = ("1d", "4h", "1h", "15m")
    higher_timeframes: tuple[str, ...] = ("1d", "4h")
    setup_timeframe: str = "1h"
    trigger_timeframe: str = "15m"
    max_setup_distance_pct: float = 3.0
    min_setup_quality_score: int = 5


class MTFAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction_context: DirectionalContext
    setup_zone: SetupZone | None = None
    trigger_context: TriggerContext | None = None
    best_opportunity: EntryOpportunity | None = None
    blocked_reasons: list[str]
    narrative: str


class IntradayStrategy:
    """
    SMC 日内交易策略实现

    三层模型:
    - 1d + 4h 定方向
    - 1h 定位 setup 区域
    - 15m 做结构触发
    """

    def __init__(self, config: Optional[IntradayConfig] = None):
        self.config = config or IntradayConfig()
        self.condition_checker = EntryConditionChecker()
        self.risk_manager = RiskManager(
            RiskParameters(
                risk_per_trade_pct=self.config.risk_per_trade_pct,
                account_balance=self.config.account_balance,
                min_risk_reward=self.config.min_risk_reward,
            )
        )

    def analyze(self) -> IntradaySignal:
        logger.info("开始执行SMC日内策略分析: %s", self.config.symbol)

        mtf_result = self._run_multi_timeframe_analysis()
        return self.analyze_result(mtf_result)

    def analyze_result(self, mtf_result: MultiTimeframeResult, close_time: datetime | None = None) -> IntradaySignal:
        analyzed_at = close_time.isoformat() if close_time else datetime.now(timezone.utc).isoformat()

        if not mtf_result.results:
            logger.error("多周期分析失败，无可用结果")
            return self._create_wait_signal(current_price=0.0, blocked_reasons=["多周期分析失败，无可用结果"], analyzed_at=analyzed_at)

        current_price = self._extract_current_price(mtf_result)
        direction_context = self._analyze_direction_context(mtf_result)

        if direction_context.bias == Bias.NEUTRAL:
            detail = f"1d score={direction_context.score}, reasons={direction_context.reasons}, blocked={direction_context.blocked_reasons}"
            logger.debug("G1-方向: NEUTRAL | %s", detail)
            return self._create_wait_signal(
                current_price, direction_context.blocked_reasons, direction_context,
                fail_gate="G1_方向", fail_detail=detail, analyzed_at=analyzed_at,
            )

        # 简化策略：只在 15m 上寻找同方向 OB
        setup_zone = self._find_best_setup_zone(
            mtf_result, direction_context.bias, current_price, timeframe="15m"
        )

        if setup_zone is None:
            detail = f"direction={direction_context.bias.name}"
            logger.debug("G2-Setup: 15m 无同向 OB | %s", detail)
            return self._create_wait_signal(
                current_price, ["15m 未发现同向 OB"], direction_context,
                fail_gate="G2_Setup", fail_detail=detail, analyzed_at=analyzed_at,
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
                current_price, ["无法计算交易计划"], direction_context,
                setup_zone=setup_zone, fail_gate="G3_计划", fail_detail="止损距离为0",
                analyzed_at=analyzed_at,
            )

        action = "LONG" if direction_context.bias == Bias.BULLISH else "SHORT"
        logger.info("生成有效交易信号: %s @ %s (现价)", action, current_price)

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
            strategy="intraday",
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

    def build_mtf_analysis(self, result: MultiTimeframeResult, current_price: float | None = None) -> MTFAnalysis:
        current_price = current_price if current_price is not None else self._extract_current_price(result)
        direction_context = self._analyze_direction_context(result)
        setup_zone = None
        trigger_context = None
        best_opportunity = None
        blocked_reasons = list(direction_context.blocked_reasons)

        if direction_context.bias != Bias.NEUTRAL:
            setup_zone = self._find_best_setup_zone(result, direction_context.bias, current_price)
            if setup_zone is None:
                blocked_reasons.append(f"{self.config.setup_timeframe} 未发现同向 OB/FVG setup")
            else:
                trigger_context = self._build_trigger_context(result, direction_context.bias, setup_zone)
                best_opportunity = self._build_opportunity(direction_context, setup_zone, trigger_context)
                blocked_reasons.extend(best_opportunity.failed_filters)

        narrative = self._build_narrative(direction_context, setup_zone, trigger_context, blocked_reasons)

        return MTFAnalysis(
            direction_context=direction_context,
            setup_zone=setup_zone,
            trigger_context=trigger_context,
            best_opportunity=best_opportunity,
            blocked_reasons=blocked_reasons,
            narrative=narrative,
        )

    def _analyze_direction_context(self, result: MultiTimeframeResult) -> DirectionalContext:
        weights = {"1d": 6, "4h": 4}
        total_weight = 0
        bull_score = 0
        bear_score = 0
        reasons: list[str] = []
        aligned: list[str] = []
        blocked: list[str] = []

        for tf in self.config.higher_timeframes:
            smc_result = result.results.get(tf)
            if smc_result is None:
                blocked.append(f"缺少 {tf} 数据")
                continue

            weight = weights.get(tf, 1)
            total_weight += weight
            tf_bias = self._score_timeframe_bias(smc_result)
            if tf_bias == Bias.BULLISH:
                bull_score += weight
                aligned.append(tf)
                reasons.append(f"{tf} 摆动与事件偏多")
            elif tf_bias == Bias.BEARISH:
                bear_score += weight
                aligned.append(tf)
                reasons.append(f"{tf} 摆动与事件偏空")
            else:
                blocked.append(f"{tf} 方向不清晰")

            position = smc_result.premium_discount.current_position
            if position == ZonePosition.DISCOUNT:
                bull_score += 1
                reasons.append(f"{tf} 位于折价/做多有利区域")
            elif position == ZonePosition.PREMIUM:
                bear_score += 1
                reasons.append(f"{tf} 位于溢价/做空有利区域")
            else:
                bull_score += 0
                bear_score += 0
                reasons.append(f"{tf} 位于均衡区")

        # 1d direction is primary (SMC principle: HTF determines bias)
        d1_result = result.results.get("1d")
        d4h_result = result.results.get("4h")
        d1_bias = self._score_timeframe_bias(d1_result) if d1_result else Bias.NEUTRAL
        d4h_bias = self._score_timeframe_bias(d4h_result) if d4h_result else Bias.NEUTRAL

        bias = Bias.NEUTRAL
        if d1_bias != Bias.NEUTRAL:
            bias = d1_bias
            if d4h_bias != Bias.NEUTRAL and d4h_bias != d1_bias:
                blocked.append(f"4h 与 1d 方向背离 (1d={d1_bias.name} 4h={d4h_bias.name})")
        elif d4h_bias != Bias.NEUTRAL:
            bias = d4h_bias
        else:
            blocked.append("1d 与 4h 方向均不清晰")

        if bias == Bias.NEUTRAL:
            confidence = 0
            logger.debug("方向诊断: bull=%d bear=%d aligned=%s blocked=%s",
                         bull_score, bear_score, aligned, blocked)
        elif d1_bias == bias and d4h_bias == bias:
            confidence = 85
        elif d1_bias == bias:
            confidence = 60
        else:
            confidence = 50

        dominant_score = max(bull_score, bear_score)

        return DirectionalContext(
            bias=bias,
            score=dominant_score,
            confidence=confidence,
            reasons=reasons,
            aligned_timeframes=aligned,
            blocked_reasons=blocked,
        )

    def _score_timeframe_bias(self, smc_result) -> Bias:
        swing_bias = smc_result.swing_state.trend
        event = smc_result.swing_state.last_event
        if swing_bias == Bias.NEUTRAL:
            return Bias.NEUTRAL
        if event is None:
            return swing_bias
        if event.bias == swing_bias:
            return swing_bias
        return Bias.NEUTRAL

    def _find_best_setup_zone(
        self,
        result: MultiTimeframeResult,
        direction: Bias,
        current_price: float,
        timeframe: str | None = None,
    ) -> SetupZone | None:
        tf = timeframe or self.config.setup_timeframe
        smc_result = result.results.get(tf)
        if smc_result is None:
            return None

        candidates: list[SetupZone] = []
        all_obs = smc_result.swing_order_blocks + smc_result.internal_order_blocks
        active_fvgs = [fvg for fvg in smc_result.fvgs if fvg.status != FVGStatus.FILLED and fvg.bias == direction]

        for ob in all_obs:
            if ob.bias != direction or ob.status == OBStatus.MITIGATED:
                continue

            distance_pct = abs(current_price - ob.mid) / ob.mid * 100 if ob.mid else 0.0
            if distance_pct > self.config.max_setup_distance_pct:
                continue

            overlap_fvg = self._find_overlapping_fvg(active_fvgs, ob)
            zone_position_ok = self._zone_position_ok(smc_result.premium_discount.current_position, direction)
            score = 0
            reasons: list[str] = []
            if ob.status == OBStatus.UNTESTED:
                score += 3
                reasons.append("OB 未测试")
            elif ob.status == OBStatus.TESTED:
                score += 1
                reasons.append("OB 已测试但未失效")

            if overlap_fvg is not None:
                score += 3
                reasons.append("OB 与同向 FVG 共振")

            if zone_position_ok:
                score += 2
                reasons.append("setup 位于合理交易区")

            if distance_pct <= 0.5:
                score += 2
                reasons.append("当前价格贴近 setup")
            elif distance_pct <= 1.0:
                score += 1
                reasons.append("当前价格接近 setup")

            source_priority = 2 if ob.source == "swing" else 1
            score += source_priority

            candidates.append(
                SetupZone(
                    timeframe=tf,
                    order_block=ob,
                    overlapping_fvg=overlap_fvg,
                    zone_quality_score=score,
                    zone_position_ok=zone_position_ok,
                    distance_to_price_pct=distance_pct,
                    reasons=reasons,
                    source_priority=source_priority,
                )
            )

        if not candidates:
            total_obs = len(all_obs)
            active = [ob for ob in all_obs if ob.bias == direction]
            mitigated = [ob for ob in active if ob.status == OBStatus.MITIGATED]
            too_far = [ob for ob in active if ob.status != OBStatus.MITIGATED and
                       abs(current_price - ob.mid) / ob.mid * 100 > self.config.max_setup_distance_pct]
            logger.debug("Setup诊断 %s: 总OB=%d 同向=%d 失效=%d 距离超标=%d",
                         direction.name, total_obs, len(active), len(mitigated), len(too_far))
            return None

        candidates.sort(
            key=lambda item: (
                item.zone_quality_score,
                item.source_priority,
                -item.distance_to_price_pct,
            ),
            reverse=True,
        )
        return candidates[0]

    def _calculate_market_trade_plan(
        self,
        order_block: OrderBlock,
        direction: Bias,
        current_price: float,
        mtf_result: MultiTimeframeResult,
        timeframe: str,
    ) -> TradePlan | None:
        """简化交易计划：现价入场，止损放在 OB 极值外一个 ATR。"""
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

        risk_amount = self.config.account_balance * (self.config.risk_per_trade_pct / 100)
        position_size = risk_amount / risk_distance

        return TradePlan(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            position_size=position_size,
            risk_reward=2.0,
        )

    def _find_overlapping_fvg(self, fvgs: list, ob: OrderBlock):
        overlaps = []
        for fvg in fvgs:
            if fvg.top >= ob.low and fvg.bottom <= ob.high:
                overlaps.append(fvg)
        if not overlaps:
            return None
        return max(overlaps, key=lambda fvg: fvg.width_atr_ratio)

    def _zone_position_ok(self, position: ZonePosition, direction: Bias) -> bool:
        if direction == Bias.BULLISH:
            return position in (ZonePosition.DISCOUNT, ZonePosition.EQUILIBRIUM)
        return position in (ZonePosition.PREMIUM, ZonePosition.EQUILIBRIUM)

    def _build_trigger_context(
        self,
        result: MultiTimeframeResult,
        direction: Bias,
        setup_zone: SetupZone,
    ) -> TriggerContext:
        smc_result = result.results.get(self.config.trigger_timeframe)
        if smc_result is None:
            return TriggerContext(
                timeframe=self.config.trigger_timeframe,
                structure_aligned=False,
                last_trigger_event=None,
                liquidity_swept=False,
                fvg_reaction=False,
                trigger_score=0,
                reasons=[f"缺少 {self.config.trigger_timeframe} 数据"],
            )

        reasons: list[str] = []
        trigger_score = 0
        event = smc_result.internal_state.last_event
        structure_aligned = bool(
            event
            and event.event_type in (EventType.CHOCH, EventType.BOS)
            and event.bias == direction
        )
        if structure_aligned:
            trigger_score += 4 if event.event_type == EventType.CHOCH else 3
            reasons.append(f"{self.config.trigger_timeframe} 出现同向 {event.event_type.name}")
        else:
            reasons.append(f"{self.config.trigger_timeframe} 缺少同向结构触发")

        liquidity_swept = self._detect_liquidity_sweep(smc_result, direction)
        if liquidity_swept:
            trigger_score += 2
            reasons.append("触发层先扫流动性后回收")

        fvg_reaction = self._detect_trigger_fvg_reaction(smc_result, direction, setup_zone)
        if fvg_reaction:
            trigger_score += 2
            reasons.append("触发层出现 FVG 反应")

        return TriggerContext(
            timeframe=self.config.trigger_timeframe,
            structure_aligned=structure_aligned,
            last_trigger_event=event.event_type.name if event else None,
            liquidity_swept=liquidity_swept,
            fvg_reaction=fvg_reaction,
            trigger_score=trigger_score,
            reasons=reasons,
        )

    def _detect_liquidity_sweep(self, smc_result, direction: Bias) -> bool:
        event = smc_result.internal_state.last_event
        if event is None:
            return False
        if direction == Bias.BULLISH:
            pivot = smc_result.internal_state.pivot_low
            return bool(pivot and event.price <= pivot.price)
        pivot = smc_result.internal_state.pivot_high
        return bool(pivot and event.price >= pivot.price)

    def _detect_trigger_fvg_reaction(self, smc_result, direction: Bias, setup_zone: SetupZone) -> bool:
        setup_ob = setup_zone.order_block
        for fvg in smc_result.fvgs:
            if fvg.status == FVGStatus.FILLED or fvg.bias != direction:
                continue
            if fvg.top >= setup_ob.low and fvg.bottom <= setup_ob.high:
                return True
        return False

    def _build_opportunity(
        self,
        direction_context: DirectionalContext,
        setup_zone: SetupZone,
        trigger_context: TriggerContext,
    ) -> EntryOpportunity:
        failed_filters: list[str] = []
        passed_hard_filters = True

        if not setup_zone.zone_position_ok:
            passed_hard_filters = False
            failed_filters.append("setup 区域位置不合理")
        if not trigger_context.structure_aligned:
            passed_hard_filters = False
            failed_filters.append(f"{trigger_context.timeframe} 缺少结构触发")

        total_score = direction_context.score + setup_zone.zone_quality_score + trigger_context.trigger_score
        confidence = min(100, total_score * 5)

        if not trigger_context.liquidity_swept:
            failed_filters.append("未出现明显流动性清扫")
        if not trigger_context.fvg_reaction:
            failed_filters.append("未出现 FVG 反应")

        return EntryOpportunity(
            direction=direction_context.bias,
            setup_zone=setup_zone,
            trigger=trigger_context,
            total_score=total_score,
            confidence=confidence,
            passed_hard_filters=passed_hard_filters,
            failed_filters=failed_filters,
        )

    def _build_narrative(
        self,
        direction_context: DirectionalContext,
        setup_zone: SetupZone | None,
        trigger_context: TriggerContext | None,
        blocked_reasons: list[str],
    ) -> str:
        if direction_context.bias == Bias.NEUTRAL:
            return "大周期方向未统一，当前以观望为主。"

        action_cn = "看多" if direction_context.bias == Bias.BULLISH else "看空"
        parts = [f"大周期 {action_cn}，方向分数 {direction_context.score}。"]

        if setup_zone is not None:
            parts.append(
                f"{setup_zone.timeframe} 已定位 {setup_zone.order_block.source} OB，"
                f"setup 评分 {setup_zone.zone_quality_score}。"
            )
        else:
            parts.append(f"{self.config.setup_timeframe} 尚未形成可交易 setup。")

        if trigger_context is not None:
            if trigger_context.structure_aligned:
                parts.append(f"{trigger_context.timeframe} 已触发 {trigger_context.last_trigger_event}。")
            else:
                parts.append(f"{trigger_context.timeframe} 仍在等待结构确认。")

        if blocked_reasons:
            parts.append(f"阻断因素: {'; '.join(dict.fromkeys(blocked_reasons))}。")

        return "".join(parts)

    def _extract_current_price(self, result: MultiTimeframeResult) -> float:
        available = {
            tf: smc for tf, smc in result.results.items()
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
        trigger_context: TriggerContext | None = None,
        conditions: list[EntryConditionResult] | None = None,
        opportunity: EntryOpportunity | None = None,
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
            confidence=opportunity.confidence if opportunity else direction_context.confidence,
            bias=direction_context.bias,
            trade_plan=None,
            conditions=conditions or [],
            analyzed_at=analyzed_at or datetime.now(timezone.utc).isoformat(),
            current_price=current_price,
            direction_context=direction_context,
            setup_zone=setup_zone,
            trigger_context=trigger_context,
            opportunity_score=opportunity.total_score if opportunity else 0,
            blocked_reasons=list(dict.fromkeys(blocked_reasons)),
            fail_gate=fail_gate,
            fail_detail=fail_detail,
        )
