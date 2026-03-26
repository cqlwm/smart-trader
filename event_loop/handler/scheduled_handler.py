import logging

from event_loop.base import Handler
from event_loop.event import Event, ScheduledEvent

logger = logging.getLogger(__name__)


class ScheduledHandler(Handler):
    """Handler that dispatches ScheduledEvent to a strategy's on_schedule method."""

    def __init__(self, strategy: object) -> None:
        super().__init__()
        self.strategy = strategy

    def run(self, event: Event) -> None:
        if not isinstance(event, ScheduledEvent):
            return

        on_schedule = getattr(self.strategy, 'on_schedule', None)
        if on_schedule is not None:
            on_schedule(event)
