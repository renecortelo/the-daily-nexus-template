import json
from datetime import UTC, date, datetime
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.config import load_settings
from audiodigest.content import minimize_editorial_text, normalize_url
from audiodigest.editorial import EditorialPipeline
from audiodigest.models import ArticleReference, SourceItem, source_prompt_dicts


class SourcePrivacyTests(TestCase):
    def source(self):
        return SourceItem(
            message_id="synthetic-mailbox-record",
            publication="Example News",
            sender="reader@example.com",
            subject="Database launch for reader@example.com",
            received_at=datetime(2026, 10, 9, tzinfo=UTC),
            email_text=("Hi, Reader!\nA database added 12 connectors and grew 35%.\n"
                        "Contact reader@example.com.\n"
                        "https://example.com/article?id=12&cs_email=synthetic\n"
                        "This email was sent to reader@example.com"),
            article_urls=["https://example.com/article?id=12&cs_stripeid=synthetic"],
            articles=[ArticleReference(
                url="https://example.com/article?id=12&cdlcid=synthetic",
                canonical_url="https://example.com/article?id=12",
                title="Database report", text="A reporter said 12 connectors are available.",
            )],
        )

    def test_prompt_uses_alias_and_preserves_reporting_without_mail_identity(self):
        source = self.source()
        payload = source_prompt_dicts([source])[0]
        encoded = json.dumps(payload)
        self.assertNotIn(source.message_id, encoded)
        self.assertNotIn(source.sender, encoded)
        self.assertNotIn("cs_email", encoded)
        self.assertNotIn("cs_stripeid", encoded)
        self.assertNotIn("cdlcid", encoded)
        self.assertEqual("source-0001", payload["message_id"])
        self.assertIn("12 connectors", payload["email_text"])
        self.assertIn("35%", payload["email_text"])
        self.assertNotIn("Hi, Reader!", payload["email_text"])
        self.assertEqual(["https://example.com/article?id=12"], payload["source_urls"])
        self.assertEqual("synthetic-mailbox-record", source.message_id)
        self.assertIn("reader@example.com", source.email_text)  # Original evidence untouched.

    def test_credentials_and_opaque_subscriber_links_are_not_fetchable(self):
        for suffix in ("token=synthetic", "access_token=synthetic", "upn=synthetic",
                       "X-Amz-Signature=synthetic", "password=synthetic"):
            self.assertEqual("", normalize_url(f"https://example.com/article?{suffix}"))
        self.assertEqual("", normalize_url("https://reader:synthetic@example.com/article"))
        self.assertEqual("", normalize_url("https://example.com:invalid/article"))

    def test_personalizer_removal_keeps_functional_query_and_article_text(self):
        self.assertEqual(
            "https://example.com/article?id=12&q=database",
            normalize_url("https://example.com/article?id=12&q=database&CS_EMAIL=synthetic"
                          "&dfp_geo=synthetic&_hsenc=synthetic&subscriber_id=synthetic"),
        )
        text = minimize_editorial_text(
            "Open-source developers described unsubscribe tools.\n"
            "See https://example.com/story?token=synthetic. Costs rose 12%."
        )
        self.assertIn("unsubscribe tools", text)
        self.assertIn("Costs rose 12%", text)
        self.assertNotIn("token=", text)

    def test_extraction_restores_internal_linkage_but_later_prompts_keep_aliases(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ()
        client = Mock()
        engine = EditorialPipeline(settings, client)

        def extract(_instruction, payload, validator, **_kwargs):
            alias = payload["sources"][0]["message_id"]
            return validator({"stories": [{
                "story_id": "database-launch", "section": "Data Engineering",
                "headline": "A database adds connectors", "facts": ["It adds 12 connectors."],
                "why_it_matters": "Teams have additional connectors.", "source_ids": [alias],
                "source_urls": ["https://example.com/article?id=12"],
                "confidence": 0.9, "rank_score": 1,
            }]}), Mock()

        client.invoke.side_effect = extract
        stories, _ = engine.extract_stories([self.source()], date(2026, 10, 9))
        self.assertEqual(["synthetic-mailbox-record"], stories[0].source_ids)
        self.assertEqual(["source-0001"], engine._story_prompt_dict(stories[0])["source_ids"])
        self.assertEqual(1, client.invoke.call_count)

    def test_unknown_source_alias_is_rejected_without_echoing_mailbox_ids(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ()
        client = Mock()
        engine = EditorialPipeline(settings, client)
        def extract(_instruction, _payload, validator, **_kwargs):
            return validator({"stories": [{
                "story_id": "database-launch", "section": "Data Engineering",
                "headline": "Database launch", "facts": ["It adds 12 connectors."],
                "why_it_matters": "Teams have connectors.", "source_ids": ["unknown-source"],
                "source_urls": [], "confidence": 0.9, "rank_score": 1,
            }]}), Mock()
        client.invoke.side_effect = extract
        with self.assertRaisesRegex(ValueError, "unknown source alias"):
            engine.extract_stories([self.source()], date(2026, 10, 9))
