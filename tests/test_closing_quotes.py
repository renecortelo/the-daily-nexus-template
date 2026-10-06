import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from audiodigest.closing_quotes import (
    ClosingQuote,
    ClosingQuoteError,
    load_closing_quotes,
    quote_for_date,
    quote_ids_from_episodes,
    select_closing_quote,
)
from audiodigest.database import StateDatabase


class ClosingQuoteTests(TestCase):
    def test_topic_relevance_and_recent_history_control_selection(self):
        tech = ClosingQuote("Build carefully.", "Author One", "https://example.com/one", ("data",))
        other = ClosingQuote(
            "Trade carefully.", "Author Two", "https://example.com/two", ("business",)
        )
        story = {"section": "DATA", "headline": "Database ETL pipeline launch", "facts": []}
        day = date(2026, 10, 5)
        selected = select_closing_quote([tech, other], day, stories=[story])
        self.assertEqual(tech, selected)
        self.assertEqual(
            other,
            select_closing_quote(
                [tech, other],
                day,
                stories=[story],
                recent_ids=[tech.quote_id],
                execution_key="second",
            ),
        )

    def test_catalog_can_support_two_daily_runs_without_thirty_episode_repetition(self):
        quotes = load_closing_quotes(Path("config/closing-quotes.json"))
        self.assertGreater(len(quotes), 30)
        history = []
        for index in range(90):
            day = date.fromordinal(date(2026, 10, 1).toordinal() + index // 2)
            selected = select_closing_quote(
                quotes,
                day,
                stories=[{"section": "DATA", "headline": "SQL analytics platform", "facts": []}],
                recent_ids=history,
                execution_key=f"run-{index}",
            )
            self.assertNotIn(selected.quote_id, history[:30])
            history.insert(0, selected.quote_id)

    def test_legacy_sources_and_new_ids_do_not_confuse_shared_book_urls(self):
        first = ClosingQuote("First.", "Author One", "https://example.com/book")
        second = ClosingQuote("Second.", "Author One", "https://example.com/book")
        third = ClosingQuote("Third.", "Author Two", "https://example.com/other")
        episodes = [
            {"status": "published", "closingQuoteId": second.quote_id},
            {"status": "published", "references": ["https://example.com/book"]},
            {"status": "published", "references": ["https://example.com/other"]},
            {"status": "failed", "closingQuoteId": first.quote_id},
        ]
        self.assertEqual(
            [second.quote_id, third.quote_id],
            quote_ids_from_episodes(
                [first, second, third],
                episodes,
            ),
        )

    def test_reserved_selection_survives_changed_topics_and_history(self):
        quotes = load_closing_quotes(Path("config/closing-quotes.json"))
        with tempfile.TemporaryDirectory() as directory:
            db = StateDatabase(Path(directory) / "state.sqlite3")
            self.assertEqual("", db.closing_quote_selection("execution"))
            selected = db.reserve_closing_quote("execution", quotes[0].quote_id)
            self.assertEqual(selected, db.reserve_closing_quote("execution", quotes[1].quote_id))
            self.assertEqual(
                quotes[0],
                select_closing_quote(
                    quotes,
                    date(2026, 10, 6),
                    recent_ids=[selected],
                    reserved_id=selected,
                ),
            )
        with self.assertRaises(ClosingQuoteError):
            select_closing_quote(quotes, date(2026, 10, 6), reserved_id="unknown")

    def test_small_custom_catalog_uses_least_recent_entry_instead_of_failing(self):
        quotes = [
            ClosingQuote(str(index), "Author", "https://example.com/book") for index in range(3)
        ]
        recent = [quote.quote_id for quote in quotes]
        self.assertEqual(
            quotes[-1], select_closing_quote(quotes, date(2026, 10, 6), recent_ids=recent)
        )

    def test_quote_selection_is_reproducible_for_episode_date(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "quotes.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "text": "First.",
                            "author": "Author One",
                            "source_url": "https://example.com/one",
                        },
                        {
                            "text": "Second.",
                            "author": "Author Two",
                            "source_url": "https://example.com/two",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            day = date(2026, 7, 27)

            self.assertEqual(
                quote_for_date(path, day),
                quote_for_date(path, day),
            )
            self.assertEqual(2, len(load_closing_quotes(path)))
