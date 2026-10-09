"""信号网格策略编排: 开仓 / 平仓 / 维护 / K线回调

仓位对象、下单、订单生命周期分别见:
`grid_position.py` / `grid_order_gateway.py` / `position_lifecycle.py`;
配置与平仓结果契约见 `signal_grid_config.py`。
本模块重新导出上述公共名, 既有 import 路径保持不变。
"""

from collections.abc import Callable
from datetime import UTC, datetime

from smart_trader import log
from smart_trader.client.ports import MarketDataPort, TradingPort
from smart_trader.execution.order_reconciler import OrderReconciler
from smart_trader.model import OrderSide, PositionSide
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow
from smart_trader.strategy import SingleTimeframeStrategy
from smart_trader.strategy.grid_order_gateway import GridOrderGateway
from smart_trader.strategy.grid_position import GridPosition, load_positions
from smart_trader.strategy.position_lifecycle import PositionLifecycle
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

# K 线周期 → 秒数; 孤儿回收阈值取 max(600, 周期时长)
_TIMEFRAME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_ORPHAN_MIN_AGE_SECONDS = 600


def _timeframe_seconds(timeframe: str) -> int:
    """把 "1m" / "1h" / "1d" 之类的周期解析成秒数"""
    unit = timeframe[-1:].lower()
    value = timeframe[:-1]
    if unit not in _TIMEFRAME_UNITS or not value.isdigit():
        raise ValueError(f"无法解析 K 线周期: {timeframe}")
    return int(value) * _TIMEFRAME_UNITS[unit]


class SignalGridStrategy(SingleTimeframeStrategy):
    def __init__(
        self,
        config: SignalGridStrategyConfig,
        trading: TradingPort,
        market: MarketDataPort,
        journal: OrderJournal,
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
            position_side=self._position_side,
        )
        self._lifecycle = PositionLifecycle(
            config=config,
            trading=trading,
            journal=journal,
            positions=lambda: self.positions,
        )

        self.on_stop_loss_order_all: Callable[[], None] = lambda: None
        self.close_position: bool = False
        self.is_running: bool = True

    # ---- 逻辑仓位登记表 ----

    @property
    def positions(self) -> list[GridPosition]:
        """逻辑仓位快照（回调已串行化, 无需锁）"""
        return list(self._positions.values())

    def add_position(self, position: GridPosition) -> None:
        """登记逻辑仓位"""
        self._positions[position.id] = position

    def remove_positions(self, positions: list[GridPosition]) -> None:
        """批量移出已归档的逻辑仓位, 防止同一根 K 线内被重复处理"""
        for position in positions:
            self._positions.pop(position.id, None)

    # ---- 方向推导 ----

    def _position_side(self) -> PositionSide:
        side = self.config.position_side
        if self.config.position_reverse:
            return (
                PositionSide.SHORT if side == PositionSide.LONG else PositionSide.LONG
            )
        return side

    def _entry_side(self) -> OrderSide:
        side = self.config.master_side
        if self.config.position_reverse:
            return side.reversal()
        return side

    def _check_max_order_stop_loss(self) -> bool:
        return (
            self.config.enable_max_order_stop_loss
            and self.config.max_order - len(self.positions) <= 1
        )

    def check_open_position(self) -> bool:
        # 检查订单是否到达上限
        if len(self.positions) >= self.config.max_order:
            return False

        # 检查是否有入场信号
        if self.config.signal and not self.config.signal.is_entry(self.klines_df):
            return False

        # 检查当前价格是否在可交易的价格区间
        if self.latest_kline_obj is None:
            return False

        close_price = self.latest_kline_obj.close
        if (
            close_price < self.config.lowest_price
            or close_price > self.config.highest_price
        ):
            return False

        if self.positions:
            recent_price_position = self.config.master_side.extremum_fun()(
                self.positions, key=lambda position: position.price
            )
        else:
            recent_price_position = None

        if (not recent_price_position) or (
            recent_price_position
            and recent_price_position.profit_and_loss_ratio(close_price)
            <= -self.config.grid_spacing_rate
        ):
            if self._check_max_order_stop_loss():
                return False
            stop_loss_rate = (
                self.config.order_stop_loss_rate
                if self.config.enable_order_stop_loss
                else 0.0
            )
            trailing_stop_rate = (
                self.config.trailing_stop_rate
                if self.config.enable_trailing_stop
                else 0.0
            )
            trailing_activation_rate = (
                self.config.trailing_stop_activation_profit_rate
                if self.config.enable_trailing_stop
                else 0.0
            )

            trailing_stop_price = None
            if self.config.enable_order_stop_loss or self.config.enable_trailing_stop:
                if self._entry_side() == OrderSide.BUY:
                    trailing_stop_price = close_price * (1 - stop_loss_rate)
                else:  # SELL
                    trailing_stop_price = close_price * (1 + stop_loss_rate)

            template = GridPositionRow(
                id=self.journal.generate_position_id(),
                strategy_key=self.config.strategy_key,
                symbol=self.config.symbol.ccxt(),
                position_side=self._position_side(),
                main_order_side=self.config.master_side,
                # 开仓参考价: 无成交（如 per_order_qty=0 的空仓位或首单未成交）时
                # 作为网格间隔 / 止盈判定的基准; created_at 用于孤儿超时回收计时
                opening_price=close_price,
                created_at=datetime.now(UTC).isoformat(),
                fixed_take_profit_rate=self.config.fixed_take_profit_rate,
                signal_min_take_profit_rate=(
                    self.config.exit_signal_take_profit_min_rate
                ),
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
                self._entry_side(),
                self.config.per_order_qty,
                close_price,
                first_price=close_price,
                template=template,
            )
            self.add_position(position)
            return True
        return False

    def _stop_loss_triggered(self, position: GridPosition, close: float) -> bool:
        if not position.enable_stop_loss or position.trailing_stop_price is None:
            return False
        if position.entry_side == OrderSide.BUY:
            return close <= position.trailing_stop_price
        return close >= position.trailing_stop_price

    def check_close_position(self) -> CloseOutcome:
        """
        触发平仓条件, 为每仓位挂出独立的出场单

        - 每仓位一张出场单, 成交可独立归因;
        - 已有未成交出场单的仓位: 普通止盈保留原挂单, 强制出场先撤单结算再重挂;
        - 只有真正完成的仓位(零成交入场单丢弃 / 出场剩余耗尽)才归档。

        @return: 本次平仓检查的结果(归档仓位 + 本根K线是否产生过平仓动作)
        """
        if self.latest_kline_obj is None:
            return CloseOutcome()

        close = self.latest_kline_obj.close
        exit_signal = (
            self.config.enable_exit_signal
            and self.config.signal
            and self.config.signal.is_exit(self.klines_df)
        )

        outcome = CloseOutcome()
        stop_loss_order_all = self._check_max_order_stop_loss() or self.close_position
        for position in self.positions:
            profit_level = position.profit_level(close)
            force_exit = stop_loss_order_all or self._stop_loss_triggered(
                position, close
            )
            normal_exit = (profit_level == 2 and self.config.fixed_rate_take_profit) or (
                profit_level == 1 and exit_signal
            )

            # 已有未成交的出场单: 普通止盈保留原挂单; 强制出场才撤单结算后重挂
            if position.has_pending_exit:
                if not force_exit:
                    continue
                outcome.acted = True
                if not self._lifecycle.force_settle_exit(position):
                    outcome.archived.append(position)
                    continue

            if not (force_exit or normal_exit):
                continue

            # 未成交入场单: 先撤剩余挂单防止继续建仓, 零成交则无可平仓位
            if position.pending_entry_orders:
                outcome.acted = True
            if self._lifecycle.settle_pending_entries(position):
                outcome.archived.append(position)
                outcome.acted = True
                continue

            self._gateway.place_and_append(
                position, position.exit_side, position.quantity, close
            )
            outcome.acted = True

        if self.close_position:
            self.close_position = False

        if stop_loss_order_all:
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
            kline.high if self._entry_side() == OrderSide.BUY else kline.low
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
        threshold = max(_ORPHAN_MIN_AGE_SECONDS, _timeframe_seconds(self.config.timeframe))
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

    def _archive(self, positions: list[GridPosition]) -> None:
        for position in positions:
            self.journal.close_position(position.id)
        self.remove_positions(positions)

    def _on_kline_finished(self):
        if self.latest_kline_obj is None:
            return

        if self.is_running:
            self._update_trailing_stop()

        # 无条件跟踪已挂出的出场单(暂停后仍必须跟踪)
        archived, _ = self._lifecycle.track_pending_exits()

        # 平仓优先于开仓: 本根K线一旦产生平仓动作, 就不再开仓
        # (check_close_position 可能因止损暂停把 is_running 置为 False, 需重新判断)
        if self.is_running:
            outcome = self.check_close_position()
            archived.extend(outcome.archived)
            if self.is_running and not outcome.acted:
                self.check_open_position()

        # 孤儿回收（每根完成K线）
        archived.extend(self._reap_orphans())

        self._archive(archived)

    def _on_kline(self):
        latest_kline = self.latest_kline_obj
        if latest_kline is None:
            return

        if not (self.config.fixed_rate_take_profit and self.config.take_profit_use_limit_order):
            return

        for position in self.positions:
            if position.quantity <= 0 and not position.pending_entry_orders:
                continue

            pending_exit = position.pending_exit_order
            if pending_exit is not None:
                exit_price = pending_exit.price
                if exit_price is not None and latest_kline.low <= exit_price <= latest_kline.high:
                    result = self.trading.query(pending_exit.order_id, self.config.symbol)
                    if result and result.status.is_closed() and self._lifecycle.settle_exit(position, pending_exit, result):
                        self._archive([position])
                continue

            if self._lifecycle.settle_pending_entries(position):
                continue
            if not position.pending_entry_orders:
                exit_price = position.price * (1 + self._entry_side().to_int() * self.config.fixed_take_profit_rate)
                self._gateway.place_and_append(
                    position,
                    position.exit_side,
                    position.quantity,
                    exit_price,
                    first_price=exit_price,
                )
