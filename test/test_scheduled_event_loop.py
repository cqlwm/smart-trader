import time
import pytest

from event_loop.event import ScheduledEvent
from event_loop.scheduled import ScheduledEventLoop
from event_loop.handler.scheduled_handler import ScheduledHandler
from event_loop.base import Handler


class SpyStrategy:
    def __init__(self) -> None:
        self.received_events: list[ScheduledEvent] = []

    def on_schedule(self, event: ScheduledEvent) -> None:
        self.received_events.append(event)


class TestScheduledEventLoop:
    def test_interval_fires(self) -> None:
        strategy = SpyStrategy()
        handler = ScheduledHandler(strategy)

        loop = ScheduledEventLoop()
        loop.add_interval("test_interval", 1)
        loop.add_handler(handler)
        loop.start()

        time.sleep(2.5)
        loop.stop()

        assert len(strategy.received_events) >= 2
        for e in strategy.received_events:
            assert e.schedule_name == "test_interval"
            assert e.timestamp > 0

    def test_multiple_intervals(self) -> None:
        strategy = SpyStrategy()
        handler = ScheduledHandler(strategy)

        loop = ScheduledEventLoop()
        loop.add_interval("fast", 1)
        loop.add_interval("slow", 10)
        loop.add_handler(handler)
        loop.start()

        time.sleep(2.5)
        loop.stop()

        fast_events = [e for e in strategy.received_events if e.schedule_name == "fast"]
        slow_events = [e for e in strategy.received_events if e.schedule_name == "slow"]
        assert len(fast_events) >= 2
        assert len(slow_events) == 1  # fires once immediately

    def test_stop_prevents_further_events(self) -> None:
        strategy = SpyStrategy()
        handler = ScheduledHandler(strategy)

        loop = ScheduledEventLoop()
        loop.add_interval("test", 1)
        loop.add_handler(handler)
        loop.start()

        time.sleep(1.5)
        loop.stop()
        count_at_stop = len(strategy.received_events)

        time.sleep(1.5)
        assert len(strategy.received_events) == count_at_stop

    def test_invalid_cron_raises(self) -> None:
        loop = ScheduledEventLoop()
        with pytest.raises(ValueError, match="Invalid cron"):
            loop.add_cron("bad", "not a cron")

    def test_cron_valid_expression_accepted(self) -> None:
        loop = ScheduledEventLoop()
        loop.add_cron("every_minute", "* * * * *")


class TestScheduledHandler:
    def test_dispatches_scheduled_event(self) -> None:
        strategy = SpyStrategy()
        handler = ScheduledHandler(strategy)
        event = ScheduledEvent(timestamp=1000, schedule_name="test")

        handler.run(event)

        assert len(strategy.received_events) == 1
        assert strategy.received_events[0].schedule_name == "test"

    def test_ignores_non_scheduled_event(self) -> None:
        from event_loop.event import Event
        strategy = SpyStrategy()
        handler = ScheduledHandler(strategy)

        handler.run(Event(timestamp=1000))

        assert len(strategy.received_events) == 0

    def test_handles_strategy_without_on_schedule(self) -> None:
        class NoScheduleStrategy:
            pass

        handler = ScheduledHandler(NoScheduleStrategy())
        event = ScheduledEvent(timestamp=1000, schedule_name="test")
        handler.run(event)  # should not raise
