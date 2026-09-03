import base64
from unittest import TestCase
from urllib.parse import quote

from audiodigest.content import (
    clean_html,
    extract_editorial_links,
    extract_editorial_links_with_stats,
    normalize_url,
)


class ContentTests(TestCase):
    def test_clean_html_removes_scripts_and_unsubscribe(self):
        value = """
        <html><script>steal()</script><body>
        <h1>Useful headline</h1><p>Useful story text.</p>
        <div class="footer">Unsubscribe from this list</div>
        </body></html>
        """
        result = clean_html(value)
        self.assertIn("Useful headline", result)
        self.assertNotIn("steal", result)
        self.assertNotIn("Unsubscribe", result)

    def test_normalize_url_strips_tracking(self):
        result = normalize_url(
            "https://EXAMPLE.com/news/story/?utm_source=mail&x=1&fbclid=secret#section"
        )
        self.assertEqual(result, "https://example.com/news/story/?x=1")

    def test_non_https_is_rejected(self):
        self.assertEqual(normalize_url("http://example.com/story"), "")
        self.assertEqual(normalize_url("mailto:test@example.com"), "")

    def test_editorial_links_rank_headlines_over_footer(self):
        value = """
        <a href="https://example.com/">Home</a>
        <a href="https://example.com/news/2026/important?utm_medium=email">
          Major public announcement changes the market
        </a>
        <a href="https://example.com/privacy">Privacy</a>
        <a href="https://example.com/unsubscribe">Unsubscribe</a>
        """
        result = extract_editorial_links(value, limit=1)
        self.assertEqual(result, ["https://example.com/news/2026/important"])

    def test_tldr_tracking_path_is_unwrapped_without_opening_tracker(self):
        target = "https://example.com/news/2026/useful-story"
        wrapped = (
            "https://tracking.tldrnewsletter.com/CL0/"
            f"{quote(target, safe='')}/1/abcdefghijklmnopqrstuvwxyz123456"
        )
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">A useful direct article headline</a>',
            limit=5,
        )

        self.assertEqual(result.urls, [target])
        self.assertEqual(result.unwrapped, 1)
        self.assertEqual(result.tracking_skipped, 0)

    def test_base64_tracking_destination_is_unwrapped(self):
        target = "https://example.com/article/useful-report"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        wrapped = f"https://click.convertkit-mail4.com/{encoded}"
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">A useful report with direct evidence</a>',
            limit=5,
        )

        self.assertEqual(result.urls, [target])
        self.assertEqual(result.unwrapped, 1)

    def test_opaque_tracking_link_is_ignored_before_fetching(self):
        wrapped = "https://link.mail.beehiiv.com/ss/c/u001.opaque-tracking-token"
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">A headline hidden behind tracking</a>',
            limit=5,
        )

        self.assertEqual(result.urls, [])
        self.assertEqual(result.tracking_skipped, 1)

    def test_nested_tracking_shortener_is_not_opened(self):
        target = "https://convertk.it/opaque-token"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        wrapped = f"https://click.convertkit-mail4.com/{encoded}"
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">A headline behind two tracking services</a>',
            limit=5,
        )

        self.assertEqual(result.urls, [])
        self.assertEqual(result.tracking_skipped, 1)

    def test_substack_public_post_is_derived_from_open_link(self):
        wrapped = "https://open.substack.com/pub/datanexus/p/ai-market-update?redirect=app-store"
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">AI market update and analysis</a>',
            limit=5,
        )

        self.assertEqual(result.urls, ["https://datanexus.substack.com/p/ai-market-update"])
        self.assertEqual(result.unwrapped, 1)

    def test_decoded_utility_destination_is_not_selected(self):
        target = quote("https://apps.apple.com/app/example", safe="")
        wrapped = (
            f"https://tracking.tldrnewsletter.com/CL0/{target}/1/abcdefghijklmnopqrstuvwxyz123456"
        )
        result = extract_editorial_links_with_stats(
            f'<a href="{wrapped}">Download the mobile application</a>',
            limit=5,
        )

        self.assertEqual(result.urls, [])
        self.assertEqual(result.utility_skipped, 1)
