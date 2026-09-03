from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class DayWindow:
    day: date
    start: datetime
    end: datetime

    @property
    def start_epoch(self) -> int:
        return int(self.start.timestamp())

    @property
    def end_epoch(self) -> int:
        return int(self.end.timestamp())


def day_window(day: date, timezone: ZoneInfo) -> DayWindow:
    start = datetime.combine(day, time.min, tzinfo=timezone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
    return DayWindow(day=day, start=start, end=end)


def previous_day_window(now: datetime, timezone: ZoneInfo) -> DayWindow:
    local_now = now.astimezone(timezone)
    return day_window(local_now.date() - timedelta(days=1), timezone)
