import logging
import threading
import time
from datetime import datetime, timezone

from croniter import croniter

from event_loop.base import DataEventLoop
from event_loop.event import ScheduledEvent

logger = logging.getLogger(__name__)


class ScheduledEventLoop(DataEventLoop):
    """Event loop that fires ScheduledEvent on interval or cron schedules."""

    def __init__(self) -> None:
        super().__init__()
        self._schedules: list[dict[str, str | int | float]] = []
        self._running = False
        self._thread: threading.Thread | None = None

    def add_interval(self, name: str, interval_seconds: int) -> None:
        self._schedules.append({
            "name": name,
            "type": "interval",
            "interval": interval_seconds,
            "last_run": 0.0,
        })
        logger.info("Added interval schedule '%s' every %ds", name, interval_seconds)

    def add_cron(self, name: str, cron_expression: str) -> None:
        if not croniter.is_valid(cron_expression):
            raise ValueError(f"Invalid cron expression: {cron_expression}")
        self._schedules.append({
            "name": name,
            "type": "cron",
            "cron": cron_expression,
            "last_run": 0.0,
        })
        logger.info("Added cron schedule '%s': %s", name, cron_expression)

    def start(self) -> None:
        if self._running:
            logger.warning("ScheduledEventLoop already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("ScheduledEventLoop started with %d schedules", len(self._schedules))

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        super().stop()
        logger.info("ScheduledEventLoop stopped")

    def _run_loop(self) -> None:
        while self._running:
            now = time.time()
            for schedule in self._schedules:
                if self._should_fire(schedule, now):
                    schedule["last_run"] = now
                    ts_ms = int(now * 1000)
                    event = ScheduledEvent(
                        timestamp=ts_ms,
                        schedule_name=str(schedule["name"]),
                    )
                    self.loop(event)
            time.sleep(0.5)

    @staticmethod
    def _should_fire(schedule: dict[str, str | int | float], now: float) -> bool:
        last_run = float(schedule["last_run"])

        if schedule["type"] == "interval":
            interval = float(schedule["interval"])
            return (now - last_run) >= interval

        if schedule["type"] == "cron":
            if last_run == 0.0:
                return True
            cron_expr = str(schedule["cron"])
            cron = croniter(cron_expr, datetime.fromtimestamp(last_run, tz=timezone.utc))
            next_time = cron.get_next(float)
            return now >= next_time

        return False
