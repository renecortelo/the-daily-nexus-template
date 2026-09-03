from datetime import UTC, date, datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from audiodigest.dates import day_window, previous_day_window


class DateWindowTests(TestCase):
    def test_normal_day_is_24_hours(self):
        window = day_window(date(2026, 7, 25), ZoneInfo("America/New_York"))
        self.assertEqual(window.end_epoch - window.start_epoch, 24 * 60 * 60)

    def test_spring_dst_day_is_23_hours(self):
        window = day_window(date(2026, 3, 8), ZoneInfo("America/New_York"))
        self.assertEqual(window.end_epoch - window.start_epoch, 23 * 60 * 60)

    def test_autumn_dst_day_is_25_hours(self):
        window = day_window(date(2026, 11, 1), ZoneInfo("America/New_York"))
        self.assertEqual(window.end_epoch - window.start_epoch, 25 * 60 * 60)

    def test_previous_day_uses_selected_calendar(self):
        now = datetime(2026, 7, 26, 7, 30, tzinfo=UTC)
        window = previous_day_window(now, ZoneInfo("America/New_York"))
        self.assertEqual(window.day, date(2026, 7, 25))
