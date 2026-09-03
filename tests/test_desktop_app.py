from unittest import TestCase

from audiodigest.desktop_app import (
    HEADER_SUBTITLE,
    WINDOW_TITLE,
    _date_with_weekday,
    _episode_date_title,
    _level_fraction,
    _player_surface_height,
)


class DesktopDateLabelsTests(TestCase):
    def test_header_subtitle_uses_nexus_name(self):
        self.assertEqual(
            "NEXUS CONSOLE 06 // PRIVATE MORNING INTELLIGENCE",
            HEADER_SUBTITLE,
        )
        self.assertNotIn("SIGNAL", HEADER_SUBTITLE)

    def test_window_title_is_only_the_product_name(self):
        self.assertEqual("The Daily Nexus", WINDOW_TITLE)

    def test_process_date_includes_weekday(self):
        self.assertEqual(
            "2026-07-28 // TUESDAY",
            _date_with_weekday("2026-07-28"),
        )

    def test_episode_title_includes_long_weekday(self):
        self.assertEqual(
            "TUESDAY, JULY 28, 2026",
            _episode_date_title("2026-07-28"),
        )

    def test_player_surface_fills_tall_viewport_without_losing_scroll_height(self):
        self.assertEqual(900, _player_surface_height(900, 640))
        self.assertEqual(900, _player_surface_height(640, 900))

    def test_level_fraction_clamps_and_exposes_selected_amount(self):
        self.assertEqual(0.8, _level_fraction(80, 0, 100))
        self.assertEqual(0.2, _level_fraction(100, 75, 200))
        self.assertEqual(0.0, _level_fraction(-10, 0, 100))
        self.assertEqual(1.0, _level_fraction(220, 75, 200))
