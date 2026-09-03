import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from audiodigest.closing_quotes import load_closing_quotes, quote_for_date


class ClosingQuoteTests(TestCase):
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
