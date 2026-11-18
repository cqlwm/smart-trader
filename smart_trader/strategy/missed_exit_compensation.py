"""停机信号补偿: 回放停机期间的完成K线, 找出最早的信号离场点

进程停机期间可能发生反向离场信号; 重启后策略只从新 K 线开始决策, 这次
信号会永久丢失。补偿器以账本中的"最后处理完成K线"为起点, 取回停机区间
的 K 线并逐根重新求值:

- 只做**发现**: 返回在哪个历史价格、对哪些仓位补挂离场单; 下单/撤单仍由
  `SignalGridStrategy` 执行;
- 只覆盖**信号离场**（信号止盈 / 信号止损）, 固定止盈、单笔止损、跟踪止损
  不参与回放;
- 取**最早**一次会触发离场的反向信号 —— 等价于"服务当时活着就该离场";
- 缺口全部回放求值（即使已找到离场点）以便信号实现的去重状态与实时一致;
- 停机缺口超过补偿窗口时放弃本轮补偿, 绝不凭猜测下单。

补偿价 = 触发信号那根 K 线的收盘价。
"""

from collections.abc import Callable

from pandas import DataFrame
from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.exchange.ports import MarketDataPort
from smart_trader.model import Kline, MarketSignal, SignalDirection, timeframe_seconds
from smart_trader.strategy.exit_policy import ExitContext, ExitPolicyChain
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig

logger = log.getLogger(__name__)

# 指标预热根数: 与策略首次拉取历史K线的根数保持一致
_DEFAULT_WARMUP_KLINES = 300
# 补偿窗口上限: 超过该缺口无法保证"最早一次信号"仍在窗口内
_DEFAULT_MAX_GAP_KLINES = 1000


class MissedExit(BaseModel):
    """一个需要在历史信号价补挂离场单的仓位

    @param force 信号止损为 True（撤单重挂）, 信号止盈为 False（已有挂单保留）
    """

    model_config = ConfigDict(extra="forbid")

    position_id: str
    force: bool


class MissedExitPlan(BaseModel):
    """一次停机补偿的发现结果

    @param exits 需要补挂离场单的仓位; 空列表表示无需补偿
    @param price 触发信号那根 K 线的收盘价（补偿限价）
    @param signal_kline_ms 触发信号那根 K 线的毫秒时间戳
    @param replayed_until_ms 已回放到的最新完成K线时间; None 表示本轮未回放
    """

    model_config = ConfigDict(extra="forbid")

    exits: list[MissedExit] = []
    price: float | None = None
    signal_kline_ms: int | None = None
    replayed_until_ms: int | None = None


class MissedExitCompensator:
    """停机信号补偿器: 只发现离场点, 不下单

    @param policies 仅包含信号类离场策略（信号止盈 / 信号止损）
    """

    def __init__(
        self,
        config: SignalGridStrategyConfig,
        market: MarketDataPort,
        positions: Callable[[], list[GridPosition]],
        policies: ExitPolicyChain,
        *,
        warmup_klines: int = _DEFAULT_WARMUP_KLINES,
        max_gap_klines: int = _DEFAULT_MAX_GAP_KLINES,
    ):
        self._config = config
        self._market = market
        self._positions = positions
        self._policies = policies
        self._warmup_klines = warmup_klines
        self._max_gap_klines = max_gap_klines

    def plan(self, last_kline_ms: int) -> MissedExitPlan:
        """回放 (last_kline_ms, 最新完成K线] 区间, 返回最早一次信号离场点

        @param last_kline_ms 账本记录的最后处理完成K线毫秒时间戳
        """
        held = [item for item in self._positions() if item.quantity > 0]
        signal = self._config.signal
        if not held or signal is None:
            return MissedExitPlan()

        klines = self._market.fetch_ohlcv(
            self._config.symbol,
            self._config.timeframe,
            self._warmup_klines + self._max_gap_klines + 1,
        )
        finished = [item for item in klines if item.finished]
        gap = [item for item in finished if item.timestamp > last_kline_ms]
        if not gap:
            return MissedExitPlan()

        if self._gap_exceeds_window(finished, last_kline_ms):
            logger.warning(
                "停机缺口超过信号补偿窗口(%d 根), 跳过补偿: 最后处理K线 %s",
                self._max_gap_klines,
                last_kline_ms,
            )
            return MissedExitPlan()

        warmup = [
            item for item in finished if item.timestamp <= last_kline_ms
        ][-self._warmup_klines :]
        base = [*warmup, *gap]
        rows = [item.to_dict() for item in base]
        result = MissedExitPlan(replayed_until_ms=gap[-1].timestamp)
        captured = False

        for index, kline in enumerate(gap, start=len(warmup)):
            frame = DataFrame(rows[: index + 1])
            market_signal = signal.evaluate(frame)
            if captured or market_signal.direction is SignalDirection.NEUTRAL:
                continue
            exits = self._exits_at(held, kline, market_signal)
            if not exits:
                continue
            result = result.model_copy(
                update={
                    "exits": exits,
                    "price": kline.close,
                    "signal_kline_ms": kline.timestamp,
                }
            )
            captured = True

        return result

    def _exits_at(
        self,
        held: list[GridPosition],
        kline: Kline,
        market_signal: MarketSignal,
    ) -> list[MissedExit]:
        """该根 K 线上按信号策略链判定需要离场的仓位"""
        ctx = ExitContext(
            positions=held,
            close=kline.close,
            market_signal=market_signal,
        )
        exits: list[MissedExit] = []
        for position in held:
            decision = self._policies.decide(position, ctx)
            if decision.should_exit:
                exits.append(
                    MissedExit(position_id=position.id, force=decision.force)
                )
        return exits

    def _gap_exceeds_window(
        self, finished: list[Kline], last_kline_ms: int
    ) -> bool:
        """最早取回的K线是否已经晚于"水位后第一根", 是则窗口未覆盖完整缺口"""
        if not finished:
            return True
        timeframe_ms = timeframe_seconds(self._config.timeframe) * 1000
        return finished[0].timestamp > last_kline_ms + timeframe_ms
