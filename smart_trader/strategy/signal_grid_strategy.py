"""信号网格策略编排: 仓位与订单管理 + 闸门/策略装配

**本类不做任何入场/离场判定**（不出现方向比较、不出现止盈止损阈值判断）——
入场由 `EntryGateChain` 判定, 离场由 `ExitPolicyChain` 判定。
本类只负责: 装配规则链、建仓下单、挂/撤出场单、结算与归档、跟踪止损落库。

仓位对象、下单、订单生命周期分别见:
`grid_position.py` / `grid_order_gateway.py` / `position_lifecycle.py`;
配置与平仓结果契约见 `signal_grid_config.py`。
本模块重新导出上述公共名, 既有 import 路径保持不变。
"""

from collections.abc import Callable
from datetime import UTC, datetime

from smart_trader import log
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import AccountPort, MarketDataPort, TradingPort
from smart_trader.execution.order_reconciler import OrderReconciler
from smart_trader.model import (
    MarketSignal,
    OrderSide,
    PositionCloseReason,
    PositionLimit,
    SignalKind,
    timeframe_seconds,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow
from smart_trader.strategy import SingleTimeframeStrategy
from smart_trader.strategy.entry_gate import (
    DirectionalSignalGate,
    EntryContext,
    EntryGate,
    EntryGateChain,
    GridSpacingGate,
    MaxOrderStopLossGate,
    MaxPositionsGate,
    PriceRangeGate,
    SignalTriggerGate,
    TrendSpacingGate,
)
from smart_trader.strategy.exit_policy import (
    ExitContext,
    ExitPolicy,
    ExitPolicyChain,
    FixedTakeProfitPolicy,
    ForceExitPolicy,
    LayeredReducePolicy,
    RestingTakeProfitPolicy,
    SignalExitPolicy,
    StopLossPolicy,
)
from smart_trader.strategy.grid_order_gateway import GridOrderGateway
from smart_trader.strategy.grid_position import GridPosition, load_positions
from smart_trader.strategy.position_lifecycle import PositionLifecycle
from smart_trader.strategy.position_reconciler import PositionReconciler
from smart_trader.strategy.signal_grid_config import (
    CloseOutcome,
    SignalGridStrategyConfig,
)

__all__ = [
    "CloseOutcome",
    "GridPosition",
    "SignalGridStrategy",
    "SignalGridStrategyConfig",
]

logger = log.getLogger(__name__)

# 孤儿回收阈值取 max(600, 周期时长)
_ORPHAN_MIN_AGE_SECONDS = 600


class SignalGridStrategy(SingleTimeframeStrategy):
    def __init__(
        self,
        config: SignalGridStrategyConfig,
        trading: TradingPort,
        market: MarketDataPort,
        journal: OrderJournal,
        account: AccountPort,
        reconcile_min_age_seconds: float = 5.0,
    ):
        super().__init__(config.timeframe, market)
        self.config = config
        self.trading = trading
        self.journal = journal

        # 启动对账: 先用订单行确认遗留的未完成下单, 再从账本恢复仓位
        self.reconciler = OrderReconciler(
            journal=journal,
            trading=trading,
            strategy_key=config.strategy_key,
            min_age_seconds=reconcile_min_age_seconds,
        )
        self.reconciler.reconcile()
        self._positions: dict[str, GridPosition] = {
            position.id: position
            for position in load_positions(journal, config.strategy_key)
        }

        self._gateway = GridOrderGateway(
            config=config,
            trading=trading,
            market=market,
            journal=journal,
            position_side=lambda: self.config.position_side,
        )
        self._lifecycle = PositionLifecycle(
            config=config,
            trading=trading,
            journal=journal,
            positions=lambda: self.positions,
        )
        # 持仓对账: 以交易所实际持仓为准收敛本地缺口(外部平仓同步)
        self._external_reductions: dict[str, float] = {}
        self._position_reconciler = PositionReconciler(
            config=config,
            account=account,
            positions=lambda: self.positions,
            lifecycle=self._lifecycle,
        )
        self._apply_position_reconcile()

        # 规则装配: 策略只做接线, 判定全在闸门/策略链内
        self._limit = PositionLimit(
            max_order=config.max_order,
            enable_stop_loss=config.enable_max_order_stop_loss,
        )
        if config.enable_max_order_stop_loss and config.max_order <= 1:
            logger.warning(
                "max_order=%s 时上限止损不适用(容量为 1 不存在`最后一格`状态), 该配置被忽略; "
                "单仓位保护请使用 enable_order_stop_loss",
                config.max_order,
            )
        self._entry_gates = self._build_entry_gates()
        self._exit_policies = self._build_exit_policies()
        self._resting_take_profit: RestingTakeProfitPolicy | None = (
            RestingTakeProfitPolicy(config.master_side)
            if config.fixed_rate_take_profit and config.take_profit_use_limit_order
            else None
        )
        self._signal_kline: str | None = None
        self._signal_cache: MarketSignal | None = None

        # 上限止损回调: 策略内部确定时机同步调用（见 check_close_position 末尾）,
        # 故用回调形态; 默认空实现, 未挂回调时不报错
        self.on_stop_loss_order_all: Callable[[], None] = lambda: None
        # 手动全平标志: **外部命令入口**, 由用户/接口置 True, 策略在下一根完成
        # K 线的 check_close_position 中消费。
        #
        # 为什么是 bool 标志而非回调: 下命令(外部线程, 任意时刻)与执行平仓(K 线
        # 驱动, 且需 close 等行情)跨层且异步, 用字段暂存意图、等策略回到自己
        # 的节奏取走。它与 on_stop_loss_order_all 故意不对称 —— 后者由策略自己
        # 同步调用, 无需跨线程。
        #
        # 边沿触发语义（一次性命令）: 消费后必须复位, 否则每根 K 线都会重复全平,
        # 变成不停挂出场单的死循环。
        self.close_position: bool = False
        self.is_running: bool = True

    # ---- 规则链装配 ----

    def _build_entry_gates(self) -> EntryGateChain:
        """按既有判定顺序装配入场闸门链"""
        signal_gate: EntryGate
        if self.config.signal_kind is SignalKind.TRIGGER:
            signal_gate = SignalTriggerGate()
        else:
            signal_gate = DirectionalSignalGate(self.config.master_side)
        return EntryGateChain(
            [
                MaxPositionsGate(self._limit),
                signal_gate,
                PriceRangeGate(self.config.lowest_price, self.config.highest_price),
                self._build_spacing_gate(),
                MaxOrderStopLossGate(self._limit),
            ]
        )

    def _build_spacing_gate(self) -> EntryGate:
        """间距口径二选一: trend_spacing_rate 非空走顺势阶梯, 否则网格间距

        两个闸门语义互斥（同一链内 AND 组合会拦死绝大多数行情）, 只装配其一。
        """
        if self.config.trend_spacing_rate is not None:
            return TrendSpacingGate(
                self.config.trend_spacing_rate, self.config.master_side
            )
        return GridSpacingGate(self.config.grid_spacing_rate, self.config.master_side)

    def _build_exit_policies(self) -> ExitPolicyChain:
        """装配离场策略链（顺序不影响合并结果）"""
        policies: list[ExitPolicy] = [
            # 上限止损与手动全平共用同一个 force 触发源。
            # 手动全平必须走 force: 仓位若已有休息止盈挂单, 非 force 决策会保留
            # 原挂单不重挂（check_close_position 中 continue）, 全平意图落空;
            # force 才会撤单结算后按最新收盘价重挂
            ForceExitPolicy(
                lambda: self._limit.stop_loss_triggered(len(self.positions))
                or self.close_position
            ),
            StopLossPolicy(),
        ]
        if self.config.fixed_rate_take_profit:
            policies.append(FixedTakeProfitPolicy())
        if self.config.enable_signal_take_profit:
            policies.append(
                SignalExitPolicy(
                    self.config.signal_kind, self.config.master_side.reversal()
                )
            )
        if self.config.enable_signal_stop_loss:
            policies.append(
                LayeredReducePolicy(
                    self.config.signal_kind, self.config.master_side.reversal()
                )
            )
        return ExitPolicyChain(policies)

    # ---- 逻辑仓位登记表 ----

    @property
    def positions(self) -> list[GridPosition]:
        """逻辑仓位快照（回调已串行化, 无需锁）"""
        return list(self._positions.values())

    @property
    def entry_gates(self) -> EntryGateChain:
        """已装配的入场闸门链（只读, 供装配契约测试验证）"""
        return self._entry_gates

    @property
    def exit_policies(self) -> ExitPolicyChain:
        """已装配的离场策略链（只读, 供装配契约测试验证）"""
        return self._exit_policies

    @property
    def position_limit(self) -> PositionLimit:
        """仓位数量上限（开仓闸门与强制离场共用的唯一权威来源）"""
        return self._limit

    def add_position(self, position: GridPosition) -> None:
        """登记逻辑仓位"""
        self._positions[position.id] = position

    def remove_positions(self, positions: list[GridPosition]) -> None:
        """批量移出已归档的逻辑仓位, 防止同一根 K 线内被重复处理"""
        for position in positions:
            self._positions.pop(position.id, None)

    # ---- 行情信号 ----

    def _current_market_signal(self) -> MarketSignal | None:
        """本根 K 线的行情信号（按 kline.datetime memo, 每根最多求值一次）

        @return 未配置信号时为 None
        """
        signal = self.config.signal
        kline = self.latest_kline_obj
        if signal is None or kline is None:
            return None
        if self._signal_kline != kline.datetime or self._signal_cache is None:
            self._signal_cache = signal.evaluate(self.klines_df)
            self._signal_kline = kline.datetime
        return self._signal_cache

    def _entry_context(self) -> EntryContext:
        kline = self.latest_kline_obj
        return EntryContext(
            positions=self.positions,
            close=kline.close if kline is not None else None,
            market_signal=self._current_market_signal(),
        )

    def _exit_context(self) -> ExitContext:
        kline = self.latest_kline_obj
        if kline is None:
            raise SmartTraderError(
                ErrorCode.MARKET_DATA_UNAVAILABLE, "尚无行情, 无法构造离场上下文"
            )
        return ExitContext(
            positions=self.positions,
            close=kline.close,
            market_signal=self._current_market_signal(),
        )

    def check_open_position(self) -> bool:
        """闸门链全部放行则建仓并下单"""
        ctx = self._entry_context()
        if not self._entry_gates.allows(ctx):
            return False
        return self._open_position(ctx.require_close())

    def _open_position(self, close_price: float) -> bool:
        """按当前收盘价建仓（闸门已放行）"""
        stop_loss_rate = (
            self.config.order_stop_loss_rate
            if self.config.enable_order_stop_loss
            else 0.0
        )
        trailing_stop_rate = (
            self.config.trailing_stop_rate if self.config.enable_trailing_stop else 0.0
        )
        trailing_activation_rate = (
            self.config.trailing_stop_activation_profit_rate
            if self.config.enable_trailing_stop
            else 0.0
        )

        trailing_stop_price = None
        if self.config.enable_trailing_stop:
            # 字段只归属跟踪止损（播种 ratchet 下限）; 固定止损由 StopLossPolicy
            # 按 仓位均价 × (1 ∓ stop_loss_rate) 实时判定, 不写死快照（Step 22）
            if self.config.master_side == OrderSide.BUY:
                trailing_stop_price = close_price * (1 - stop_loss_rate)
            else:  # SELL
                trailing_stop_price = close_price * (1 + stop_loss_rate)

        template = GridPositionRow(
            id=self.journal.generate_position_id(),
            strategy_key=self.config.strategy_key,
            symbol=self.config.symbol.ccxt(),
            position_side=self.config.position_side,
            main_order_side=self.config.master_side,
            # 开仓参考价: 无成交（如 per_order_qty=0 的空仓位或首单未成交）时
            # 作为网格间隔 / 止盈判定的基准; created_at 用于孤儿超时回收计时
            opening_price=close_price,
            created_at=datetime.now(UTC).isoformat(),
            fixed_take_profit_rate=self.config.fixed_take_profit_rate,
            # 信号止盈门槛已随 Step 24 删除（浮盈即止盈）; 行列保留为 schema 兼容
            signal_min_take_profit_rate=0.0,
            enable_stop_loss=self.config.enable_order_stop_loss,
            stop_loss_rate=stop_loss_rate,
            enable_trailing_stop=self.config.enable_trailing_stop,
            trailing_stop_rate=trailing_stop_rate,
            trailing_stop_activation_rate=trailing_activation_rate,
            trailing_stop_price=trailing_stop_price,
        )

        if self.config.per_order_qty == 0:
            # 零数量: 只登记一个空仓位（无首单）
            self.journal.upsert_position(template)
            self.add_position(GridPosition.from_row(template, []))
            return True

        position = GridPosition.from_row(template, [])
        self._gateway.place_and_append(
            position,
            self.config.master_side,
            self.config.per_order_qty,
            close_price,
            first_price=close_price,
            template=template,
        )
        self.add_position(position)
        return True

    def check_close_position(self) -> CloseOutcome:
        """
        触发平仓条件, 为每仓位挂出独立的出场单

        - 每仓位一张出场单, 成交可独立归因;
        - 已有未成交出场单的仓位: 只有 force 决策才撤单结算再重挂;
        - 只有真正完成的仓位(零成交入场单丢弃 / 出场剩余耗尽)才归档。

        @return: 本次平仓检查的结果(按归档语义分组的仓位 + 本根K线是否产生过平仓动作)
        """
        if self.latest_kline_obj is None:
            return CloseOutcome()

        ctx = self._exit_context()
        # 上限止损是仓位外部强制; 手动全平是用户主动行为 —— 两个独立触发源
        stop_loss_reached = self._limit.stop_loss_triggered(len(self.positions))

        outcome = CloseOutcome()
        for position in self.positions:
            decision = self._exit_policies.decide(position, ctx)

            # 已有未成交的出场单: 非强制决策保留原挂单, 强制才撤单结算后重挂
            if position.has_pending_exit:
                if not decision.force:
                    continue
                outcome.acted = True
                if not self._lifecycle.force_settle_exit(position):
                    outcome.exit_settled.append(position)
                    continue

            if not decision.should_exit:
                continue

            # 未成交入场单: 先撤剩余挂单防止继续建仓; 撤后仓位归零即入场丢弃归档
            # (无入场单时不得判 entry_discarded —— 出场结算耗尽的仓位已由
            #  track_pending_exits 以 EXIT_SETTLED 归档, 这里重复判定会覆盖原因)
            if position.pending_entry_orders:
                outcome.acted = True
                if self._lifecycle.settle_pending_entries(position):
                    outcome.entry_discarded.append(position)
                    outcome.acted = True
                    continue

            exit_qty = self._exit_quantity(position)
            if exit_qty <= 0:
                # 可平量被外部平仓耗尽却未被本轮对账归档(浮点边界): 不下单
                continue
            self._gateway.place_and_append(
                position, position.exit_side, exit_qty, ctx.close
            )
            outcome.acted = True

        if self.close_position:
            # 一次性命令必须消费即清: 不复位则下一根 K 线再次全平, 无限重复挂出场单
            self.close_position = False

        # 仅"达到上限止损"触发回调与暂停; 手动全平不触发（已修复的缺陷）。
        # stop_loss_reached 在上面的循环前单独算, 故意不含 close_position ——
        # 用户点一次全平若顺带触发 on_stop_loss_order_all + is_running=False,
        # 策略就此死掉, 再也不会开新仓; 用户要的是"清仓后继续盯盘"。
        if stop_loss_reached:
            self.on_stop_loss_order_all()
            if self.config.paused_after_stop_loss:
                self.is_running = False

        return outcome

    # ---- 每根完成K线的维护 ----

    def _update_trailing_stop(self) -> None:
        kline = self.latest_kline_obj
        if kline is None:
            return
        extremum_price = (
            kline.high if self.config.master_side == OrderSide.BUY else kline.low
        )
        for position in self.positions:
            if not position.enable_trailing_stop or position.trailing_stop_price is None:
                continue

            activation_price = position.price * (
                1
                + position.trailing_stop_activation_rate * position.entry_side.to_int()
            )
            if position.entry_side.compare_fun(and_eq=True)(
                extremum_price, activation_price
            ):
                new_stop_price = extremum_price * (
                    1 - position.trailing_stop_rate * position.entry_side.to_int()
                )
                position.trailing_stop_price = position.entry_side.reversal().extremum_fun()(
                    position.trailing_stop_price, new_stop_price
                )
                self.journal.upsert_position(position.to_row())

    def _reap_orphans(self) -> list[GridPosition]:
        """
        孤儿回收: active=1 且 quantity<=0 且无任何存活订单, 且存在时长超过
        max(10 分钟, 策略 K 线周期时长) 的空仓位才回收（置 active=0）。
        """
        threshold = max(
            _ORPHAN_MIN_AGE_SECONDS, timeframe_seconds(self.config.timeframe)
        )
        archived: list[GridPosition] = []
        for position in self.positions:
            if position.quantity > 0 or position.has_live_orders:
                continue
            if self._orphan_expired(position, threshold):
                archived.append(position)
        return archived

    @staticmethod
    def _orphan_expired(position: GridPosition, threshold_seconds: int) -> bool:
        try:
            created = datetime.fromisoformat(position.created_at)
        except ValueError:
            return False
        age = (datetime.now(UTC) - created).total_seconds()
        return age > threshold_seconds

    def _archive(
        self, positions: list[GridPosition], reason: PositionCloseReason
    ) -> None:
        """归档仓位: `active=0` 与归档原因同语句落库（审计可查）"""
        for position in positions:
            self.journal.close_position(position.id, reason)
        self.remove_positions(positions)

    def _apply_position_reconcile(self) -> list[GridPosition]:
        """执行持仓对账并消费结果: 归档外部全平仓位, 记录部分平仓扣减量"""
        result = self._position_reconciler.reconcile()
        for position in result.archived:
            self._archive([position], PositionCloseReason.EXTERNAL_RECONCILED)
        self._external_reductions = result.adjustments
        return result.archived

    def _exit_quantity(self, position: GridPosition) -> float:
        """实际可平量 = 账本派生量 - 被外部平掉的量(未对账到时为 0)"""
        return position.quantity - self._external_reductions.get(position.id, 0.0)

    def _on_kline_finished(self):
        latest_kline = self.latest_kline_obj
        if latest_kline is None:
            return

        if self.is_running:
            self._update_trailing_stop()

        # 归档按来源分组收集, 落库时各自携带 closed_reason（审计可查）
        archives: list[tuple[GridPosition, PositionCloseReason]] = []

        # 无条件跟踪已挂出的出场单(暂停后仍必须跟踪)
        settled, _ = self._lifecycle.track_pending_exits()
        archives.extend((position, PositionCloseReason.EXIT_SETTLED) for position in settled)

        # 持仓对账(暂停后仍必须执行): 以交易所实际持仓为准收敛本地缺口
        archives.extend(
            (position, PositionCloseReason.EXTERNAL_RECONCILED)
            for position in self._apply_position_reconcile()
        )

        # 平仓优先于开仓: 本根K线一旦产生平仓动作, 就不再开仓
        # (check_close_position 可能因止损暂停把 is_running 置为 False, 需重新判断)
        if self.is_running:
            outcome = self.check_close_position()
            archives.extend(
                (position, PositionCloseReason.EXIT_SETTLED)
                for position in outcome.exit_settled
            )
            archives.extend(
                (position, PositionCloseReason.ENTRY_DISCARDED)
                for position in outcome.entry_discarded
            )
            # 对账归档同样是敞口减少动作: 收敛当根不开新仓
            if self.is_running and not outcome.acted and not archives:
                self.check_open_position()

        # 孤儿回收（每根完成K线）
        archives.extend(
            (position, PositionCloseReason.ORPHAN_REAPED)
            for position in self._reap_orphans()
        )

        for position, reason in archives:
            self._archive([position], reason)

    def _on_kline(self):
        """盘中路径: 结算入场单成交, 跟踪休息中的出场单, 必要时挂出休息限价止盈单

        入场单结算是记账职责, 每 tick 无条件执行 —— 不受限价止盈开关影响
        （Step 21 修复: 原实现以 resting_take_profit is None 整体短路,
        未开启限价止盈的配置下入场单成交永远无人结算）。
        """
        latest_kline = self.latest_kline_obj
        if latest_kline is None:
            return

        resting_take_profit = self._resting_take_profit
        for position in self.positions:
            # 入场单成交结算: 只回写事实, 不撤单（撤单语义属于出场流程）
            self._lifecycle.settle_entry_fills(position)

            if resting_take_profit is None:
                continue  # 限价止盈未启用: 无休息单可跟踪/挂出

            pending_exit = position.pending_exit_order
            if pending_exit is not None:
                exit_price = pending_exit.price
                if exit_price is not None and latest_kline.low <= exit_price <= latest_kline.high:
                    result = self.trading.query(pending_exit.order_id, self.config.symbol)
                    if result and result.status.is_closed() and self._lifecycle.settle_exit(position, pending_exit, result):
                        self._archive([position], PositionCloseReason.EXIT_SETTLED)
                continue

            if position.quantity <= 0 or position.pending_entry_orders:
                continue  # 无可平量或入场单未了结: 不挂休息止盈单
            # 挂单前实时对账: 上次对账后外部可能又平了仓(挂单每仓位仅一次, 低频)
            self._apply_position_reconcile()
            if position.id not in self._positions:
                continue  # 对账已把本仓位内部归档
            exit_qty = self._exit_quantity(position)
            if exit_qty <= 0:
                continue  # 可平量被外部平仓耗尽: 不挂休息止盈单
            exit_price = resting_take_profit.limit_price(position)
            self._gateway.place_and_append(
                position,
                position.exit_side,
                exit_qty,
                exit_price,
                first_price=exit_price,
            )
