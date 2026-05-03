from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict


class CronField(BaseModel):
    model_config = ConfigDict(frozen=True)
    allowed: frozenset[int]
    wildcard: bool

    def matches(self, value: int) -> bool:
        return value in self.allowed


class CronSchedule(BaseModel):
    minute: CronField
    hour: CronField
    day_of_month: CronField
    month: CronField
    day_of_week: CronField

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("cron 表达式必须包含 5 段: 分 时 日 月 周")

        return cls(
            minute=_parse_field(parts[0], 0, 59, "minute"),
            hour=_parse_field(parts[1], 0, 23, "hour"),
            day_of_month=_parse_field(parts[2], 1, 31, "day_of_month"),
            month=_parse_field(parts[3], 1, 12, "month"),
            day_of_week=_parse_field(parts[4], 0, 7, "day_of_week", normalize_weekday=True),
        )

    def matches(self, dt: datetime) -> bool:
        weekday = (dt.weekday() + 1) % 7
        dom_match = self.day_of_month.matches(dt.day)
        dow_match = self.day_of_week.matches(weekday)

        if self.day_of_month.wildcard and self.day_of_week.wildcard:
            day_match = True
        elif self.day_of_month.wildcard:
            day_match = dow_match
        elif self.day_of_week.wildcard:
            day_match = dom_match
        else:
            day_match = dom_match or dow_match

        return (
            self.minute.matches(dt.minute)
            and self.hour.matches(dt.hour)
            and self.month.matches(dt.month)
            and day_match
        )

    def next_run(self, now: datetime, *, allow_current: bool = True) -> datetime:
        candidate = now.replace(second=0, microsecond=0)
        if (
            not allow_current
            or now.second != 0
            or now.microsecond != 0
            or not self.matches(candidate)
        ):
            candidate += timedelta(minutes=1)

        limit = candidate + timedelta(days=366 * 5)
        while candidate <= limit:
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("未能在 5 年范围内找到下一次 cron 触发时间")


def _parse_field(
    raw: str,
    minimum: int,
    maximum: int,
    label: str,
    *,
    normalize_weekday: bool = False,
) -> CronField:
    wildcard = raw == "*"
    allowed: set[int] = set()

    for part in raw.split(","):
        if not part:
            raise ValueError(f"{label} 字段存在空片段")
        allowed.update(
            _expand_part(
                part,
                minimum,
                maximum,
                label,
                normalize_weekday=normalize_weekday,
            )
        )

    if not allowed:
        raise ValueError(f"{label} 字段不能为空")
    return CronField(allowed=frozenset(allowed), wildcard=wildcard)


def _expand_part(
    part: str,
    minimum: int,
    maximum: int,
    label: str,
    *,
    normalize_weekday: bool = False,
) -> set[int]:
    if "/" in part:
        base, step_raw = part.split("/", 1)
        step = int(step_raw)
        if step <= 0:
            raise ValueError(f"{label} 字段步长必须大于 0")
    else:
        base = part
        step = 1

    if base == "*":
        start = minimum
        end = maximum
    elif "-" in base:
        start_raw, end_raw = base.split("-", 1)
        start = _parse_int(start_raw, minimum, maximum, label, normalize_weekday=normalize_weekday)
        end = _parse_int(end_raw, minimum, maximum, label, normalize_weekday=normalize_weekday)
        if start > end:
            raise ValueError(f"{label} 字段范围无效: {base}")
    else:
        value = _parse_int(base, minimum, maximum, label, normalize_weekday=normalize_weekday)
        start = value
        end = value

    return set(range(start, end + 1, step))


def _parse_int(
    raw: str,
    minimum: int,
    maximum: int,
    label: str,
    *,
    normalize_weekday: bool = False,
) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} 字段值无效: {raw}") from exc

    if normalize_weekday and value == 7:
        value = 0

    if value < minimum or value > maximum:
        raise ValueError(f"{label} 字段超出范围: {raw}")
    return value
