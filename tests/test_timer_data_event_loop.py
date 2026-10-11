"""TimerDataEventLoop 定时调度测试

- 调度数学 `_next_events` 是纯函数, 直接单测(无线程、无 IO);
- 拉取/分发用假数据源与假时钟(不触网, 无真实长 sleep);
- start/stop 用真实时钟 + 1s 周期验证线程语义。
"""

import logging
import threading
import time
from collections import deque
from typing import Any

import pytest

from smart_trader.data_event_loop import KlineScheduleKind, TimerDataEventLoop
from smart_trader.model import Kline, Symbol
from smart_trader.strategy import SingleTimeframeStrategy
from smart_trader.task.strategy_task import StrategyTask
from tests.fakes import FakeMarketDataPort, make_kline

SYMBOL = Symbol(base="DOGE", quote="USDT")
BTC_SYMBOL = Symbol(base="BTC", quote="USDT")

# 对齐 1m / 5m 边界的基准时刻(秒)
BASE = 1_700_001_000.0


class FakeClock:
    """可控时钟: 测试直接推进"""

    def __init__(self, start: float = BASE) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StepClock:
    """按预设序列返回时间的时钟, 序列耗尽后停在最后一个值"""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> float:
        if self._values:
            return self._values.pop(0)
        return self._last


class ScriptedKlineMarket(FakeMarketDataPort):
    """按 limit 模拟真实行情: limit<=1 返回进行中K线, limit>=2 返回 [进行中, 已完成]

    responses 非空时按序弹出(Exception 则抛出), 用尽后回落默认行为。
    """

    def __init__(self, timeframe: str = "1m") -> None:
        super().__init__()
        self._timeframe = timeframe
        self.calls: list[tuple[Symbol, str, int]] = []
        self.responses: deque[list[Kline] | Exception] = deque()

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Kline]:
        self.calls.append((symbol, timeframe, limit))
        if self.responses:
            response = self.responses.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        current = make_kline(timeframe=self._timeframe, finished=False, close=0.16)
        if limit <= 1:
            return [current]
        return [
            current,
            make_kline(timeframe=self._timeframe, finished=True, close=0.15),
        ]


class CallbackRecordingStrategy(SingleTimeframeStrategy):
    """记录 run 入参与回调触发, 验证定时分发能走到策略回调"""

    def __init__(self, timeframe: str = "1m") -> None:
        super().__init__(timeframe, FakeMarketDataPort())
        self.received: list[Kline] = []
        self.callbacks: list[str] = []

    def run(self, kline: Kline) -> None:
        self.received.append(kline)
        super().run(kline)

    def _on_kline(self) -> None:
        self.callbacks.append("on_kline")

    def _on_kline_finished(self) -> None:
        self.callbacks.append("on_kline_finished")


def build_loop(
    subscriptions: list[tuple[Symbol, str]] | None = None,
    *,
    clock: Any = None,
    market: ScriptedKlineMarket | None = None,
    **overrides: Any,
) -> tuple[TimerDataEventLoop, FakeClock, ScriptedKlineMarket]:
    """构建默认 1m 订阅的定时循环(快轮询、零重试间隔, 便于测试)"""
    fake_clock = clock if clock is not None else FakeClock()
    fake_market = market if market is not None else ScriptedKlineMarket()
    loop = TimerDataEventLoop(
        subscriptions=subscriptions if subscriptions is not None else [(SYMBOL, "1m")],
        market=fake_market,
        clock=fake_clock,
        poll_interval=0.001,
        **overrides,
    )
    return loop, fake_clock, fake_market


class TestConstruction:
    def test_empty_subscriptions_rejected(self):
        with pytest.raises(ValueError, match="subscriptions"):
            TimerDataEventLoop(subscriptions=[], market=ScriptedKlineMarket())

    def test_invalid_timeframe_rejected(self):
        with pytest.raises(ValueError, match="1x"):
            TimerDataEventLoop(
                subscriptions=[(SYMBOL, "1x")], market=ScriptedKlineMarket()
            )

    def test_fetch_delay_must_be_less_than_half_timeframe(self):
        with pytest.raises(ValueError, match="fetch_delay"):
            TimerDataEventLoop(
                subscriptions=[(SYMBOL, "1m")],
                market=ScriptedKlineMarket(),
                fetch_delay=30.0,
            )

    def test_default_fetch_delay_rejects_sub_minute_timeframe(self):
        """默认 2s 延迟对 1s 周期来说已越过半程, 必须构造即报错"""
        with pytest.raises(ValueError, match="fetch_delay"):
            TimerDataEventLoop(
                subscriptions=[(SYMBOL, "1s")], market=ScriptedKlineMarket()
            )


class TestNextEvents:
    """调度数学: 最早到期事件 + 并列事件同时发, 错过的不补发"""

    def test_exactly_at_boundary_fetches_just_closed_kline(self):
        """恰在边界启动: 已完成K线的拉取时刻(边界+延迟)仍在未来, 应被调度"""
        loop, _, _ = build_loop(fetch_delay=2.0)

        fire_at, events = loop._next_events(BASE)

        assert fire_at == BASE + 2
        assert events == [("1m", KlineScheduleKind.BOUNDARY)]

    def test_within_delay_window_after_boundary(self):
        loop, _, _ = build_loop(fetch_delay=2.0)

        fire_at, events = loop._next_events(BASE + 1)

        assert fire_at == BASE + 2
        assert events == [("1m", KlineScheduleKind.BOUNDARY)]

    def test_midpoint_scheduled_at_half_timeframe(self):
        loop, _, _ = build_loop(fetch_delay=2.0)

        fire_at, events = loop._next_events(BASE + 5)

        assert fire_at == BASE + 30
        assert events == [("1m", KlineScheduleKind.MIDPOINT)]

    def test_missed_midpoint_is_skipped(self):
        """中点已过: 只调度未来事件(下一边界)"""
        loop, _, _ = build_loop(fetch_delay=2.0)

        fire_at, events = loop._next_events(BASE + 31)

        assert fire_at == BASE + 62
        assert events == [("1m", KlineScheduleKind.BOUNDARY)]

    def test_just_fired_event_is_not_rescheduled(self):
        """幂等: 恰到 fire_at 时刻, 刚触发的事件不再进入候选"""
        loop, _, _ = build_loop(fetch_delay=2.0)

        fire_at, events = loop._next_events(BASE + 2)

        assert fire_at == BASE + 30
        assert events == [("1m", KlineScheduleKind.MIDPOINT)]

    def test_tied_boundaries_across_timeframes_fire_together(self):
        """1m 与 5m 边界重合: 同一时刻到期的所有事件一并返回"""
        loop, _, _ = build_loop(
            subscriptions=[(SYMBOL, "1m"), (BTC_SYMBOL, "5m")], fetch_delay=2.0
        )

        fire_at, events = loop._next_events(BASE)

        assert fire_at == BASE + 2
        assert events == [
            ("1m", KlineScheduleKind.BOUNDARY),
            ("5m", KlineScheduleKind.BOUNDARY),
        ], "并列事件必须同刻齐发, 否则后到的时间框架会被跳过"

    def test_multi_timeframe_takes_earliest_event(self):
        loop, _, _ = build_loop(
            subscriptions=[(SYMBOL, "1m"), (BTC_SYMBOL, "5m")], fetch_delay=2.0
        )

        fire_at, events = loop._next_events(BASE + 40)

        assert fire_at == BASE + 62
        assert events == [("1m", KlineScheduleKind.BOUNDARY)]

    def test_tie_order_follows_subscription_order(self):
        loop, _, _ = build_loop(
            subscriptions=[(BTC_SYMBOL, "5m"), (SYMBOL, "1m")], fetch_delay=2.0
        )

        _, events = loop._next_events(BASE)

        assert events == [
            ("5m", KlineScheduleKind.BOUNDARY),
            ("1m", KlineScheduleKind.BOUNDARY),
        ]


class TestWaitUntil:
    def test_returns_true_once_clock_reaches_target(self):
        loop, _, _ = build_loop(clock=StepClock([BASE, BASE + 60]))
        loop.running = True  # 等效 start() 的运行态

        assert loop._wait_until(BASE + 60) is True

    def test_stop_interrupts_wait_and_returns_false(self):
        loop, _, _ = build_loop()  # 时钟停在 BASE, 永远到不了 target
        loop.running = True
        result: list[bool] = []

        def waiting() -> None:
            result.append(loop._wait_until(BASE + 60))

        thread = threading.Thread(target=waiting)
        thread.start()
        time.sleep(0.05)
        loop.stop()
        thread.join(timeout=1.0)

        assert not thread.is_alive(), "stop() 必须立即唤醒等待"
        assert result == [False]

    def test_returns_false_if_already_stopped(self):
        loop, _, _ = build_loop()
        loop.stop()

        assert loop._wait_until(BASE + 60) is False


class TestFetchFinishedKline:
    def test_returns_last_finished_kline(self):
        loop, _, market = build_loop()

        kline = loop._fetch_finished_kline(SYMBOL, "1m", 0)

        assert kline is not None and kline.finished is True
        assert market.calls == [(SYMBOL, "1m", 2)], "已完成K线必须以 limit=2 拉取"

    def test_returns_none_when_no_finished_kline(self):
        """交易所尚未滚出已完成K线(本地时钟落后): 不等待重试, 直接跳过本周期"""
        loop, _, market = build_loop()
        market.responses.append([make_kline(finished=False)])

        kline = loop._fetch_finished_kline(SYMBOL, "1m", 0)

        assert kline is None
        assert len(market.calls) == 1, "不重试, 拉一次就放弃"

    def test_fetch_error_returns_none(self):
        loop, _, market = build_loop()
        market.responses.append(RuntimeError("网络抖动"))

        kline = loop._fetch_finished_kline(SYMBOL, "1m", 0)

        assert kline is None
        assert len(market.calls) == 1, "单次失败不重试, 循环自行在下个边界继续"

    def test_returns_none_when_kline_already_dispatched(self):
        """同一根K线(按时间戳)只分发一次 —— 时钟漂移时防止重复触发交易决策"""
        loop, _, _ = build_loop()

        first = loop._fetch_finished_kline(SYMBOL, "1m", 0)
        second = loop._fetch_finished_kline(SYMBOL, "1m", 0)

        assert first is not None
        assert second is None, "已分发过的K线必须返回 None"

    def test_dispatches_again_when_timestamp_advances(self):
        loop, _, market = build_loop()
        market.responses.append(
            [make_kline(finished=False), make_kline(finished=True, timestamp=456)]
        )
        market.responses.append(
            [make_kline(finished=False), make_kline(finished=True, timestamp=516)]
        )

        first = loop._fetch_finished_kline(SYMBOL, "1m", 456)
        second = loop._fetch_finished_kline(SYMBOL, "1m", 516)

        assert first is not None and second is not None, "新K线必须正常分发"

    def test_dedupe_is_isolated_per_subscription(self):
        loop, _, _ = build_loop()

        first = loop._fetch_finished_kline(SYMBOL, "1m", 0)
        other = loop._fetch_finished_kline(BTC_SYMBOL, "1m", 0)

        assert first is not None and other is not None, "去重状态必须按 (symbol, timeframe) 隔离"

    def test_timestamp_mismatch_is_logged_as_drift(
        self, caplog: pytest.LogCaptureFixture
    ):
        """交易所K线时间戳与本地边界不一致 = 时钟漂移信号, 必须留日志"""
        loop, _, market = build_loop()
        market.responses.append(
            [make_kline(finished=False), make_kline(finished=True, timestamp=123)]
        )

        with caplog.at_level(logging.WARNING):
            loop._fetch_finished_kline(SYMBOL, "1m", expected_open_ms=456)

        assert any(
            "时钟漂移" in record.message for record in caplog.records
        ), "时间戳不一致必须记录警告"

    def test_matching_timestamp_has_no_drift_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        loop, _, market = build_loop()
        market.responses.append(
            [make_kline(finished=False), make_kline(finished=True, timestamp=456)]
        )

        with caplog.at_level(logging.WARNING):
            loop._fetch_finished_kline(SYMBOL, "1m", expected_open_ms=456)

        assert not any("时钟漂移" in record.message for record in caplog.records)


class TestFetchCurrentKline:
    def test_returns_in_progress_kline(self):
        loop, _, market = build_loop()

        kline = loop._fetch_current_kline(SYMBOL, "1m")

        assert kline is not None and kline.finished is False
        assert market.calls == [(SYMBOL, "1m", 1)], "盘中K线必须以 limit=1 拉取"

    def test_finished_kline_at_midpoint_is_rejected(
        self, caplog: pytest.LogCaptureFixture
    ):
        """中点拉到已收盘K线 = 时钟漂移, 不能当作盘中更新分发"""
        loop, _, market = build_loop()
        market.responses.append([make_kline(finished=True)])

        with caplog.at_level(logging.WARNING):
            kline = loop._fetch_current_kline(SYMBOL, "1m")

        assert kline is None
        assert any("时钟漂移" in record.message for record in caplog.records)

    def test_empty_result_returns_none(self):
        loop, _, market = build_loop()
        market.responses.append([])

        assert loop._fetch_current_kline(SYMBOL, "1m") is None

    def test_fetch_error_returns_none(self):
        loop, _, market = build_loop()
        market.responses.append(RuntimeError("网络抖动"))

        assert loop._fetch_current_kline(SYMBOL, "1m") is None


class TestFire:
    def test_boundary_dispatches_finished_kline_and_triggers_on_kline_finished(self):
        loop, _, _ = build_loop()
        strategy = CallbackRecordingStrategy()
        loop.add_task(StrategyTask(SYMBOL, strategy))
        loop.running = True  # 等效 start() 的运行态, 但不进入阻塞调度

        loop._fire("1m", KlineScheduleKind.BOUNDARY, fire_at=BASE + 2)
        loop.executor.shutdown(wait=True)

        assert len(strategy.received) == 1
        assert strategy.received[0].finished is True
        # 既有契约(MultiTimeframeStrategy.run): 完成K线先走盘中簿记(_call_on_kline)
        # 再触发交易决策, 与 WS 路径对完成K线的处理完全一致
        assert strategy.callbacks == ["on_kline", "on_kline_finished"], (
            "边界事件必须触发 on_kline_finished"
        )

    def test_midpoint_dispatches_in_progress_kline_and_triggers_on_kline(self):
        loop, _, _ = build_loop()
        strategy = CallbackRecordingStrategy()
        loop.add_task(StrategyTask(SYMBOL, strategy))
        loop.running = True

        loop._fire("1m", KlineScheduleKind.MIDPOINT, fire_at=BASE + 30)
        loop.executor.shutdown(wait=True)

        assert len(strategy.received) == 1
        assert strategy.received[0].finished is False
        assert strategy.callbacks == ["on_kline"], "中点事件必须触发 on_kline"

    def test_fetch_failure_skips_dispatch_without_crashing(self):
        loop, _, market = build_loop()
        strategy = CallbackRecordingStrategy()
        loop.add_task(StrategyTask(SYMBOL, strategy))
        loop.running = True
        market.responses.append(RuntimeError("网络抖动"))

        loop._fire("1m", KlineScheduleKind.BOUNDARY, fire_at=BASE + 2)
        loop.executor.shutdown(wait=True)

        assert strategy.received == [], "拉取耗尽后必须跳过本周期, 不能分发旧数据"

    def test_only_subscriptions_of_the_fired_timeframe_are_fetched(self):
        loop, _, market = build_loop(
            subscriptions=[(SYMBOL, "1m"), (BTC_SYMBOL, "5m")]
        )
        loop.running = True

        loop._fire("1m", KlineScheduleKind.BOUNDARY, fire_at=BASE + 2)
        loop.executor.shutdown(wait=True)

        assert [call[1] for call in market.calls] == ["1m"], (
            "只有被触发的周期才会拉取"
        )


class TestStartStop:
    def test_start_dispatches_klines_and_stop_exits_promptly(self):
        """真实时钟线程语义: 1s 周期下能收到K线, stop() 立即退出"""
        market = ScriptedKlineMarket(timeframe="1s")
        strategy = CallbackRecordingStrategy(timeframe="1s")
        loop = TimerDataEventLoop(
            subscriptions=[(SYMBOL, "1s")],
            market=market,
            clock=time.time,
            fetch_delay=0.05,
            poll_interval=0.005,
        )
        loop.add_task(StrategyTask(SYMBOL, strategy))

        thread = threading.Thread(target=loop.start)
        thread.start()
        deadline = time.time() + 3.0
        while not strategy.received and time.time() < deadline:
            time.sleep(0.01)
        loop.stop()
        thread.join(timeout=1.0)

        assert strategy.received, "3 秒内必须至少分发一根K线"
        assert not thread.is_alive(), "stop() 必须立即结束调度线程"
        assert loop.running is False
