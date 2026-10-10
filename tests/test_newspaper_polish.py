from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from audiodigest.config import load_settings
from audiodigest.editorial import _bullet_repeats_article, _deduplicate_newspaper_articles
from audiodigest.models import (
    DataValidationError,
    NewspaperArticle,
    NewspaperIssue,
    NewspaperVisual,
    NewspaperVisualItem,
)
from audiodigest.newspaper import (
    NewspaperRenderer,
    NewspaperRenderError,
    _bullet_adds_distinct_information,
)


class NewspaperPolishTests(TestCase):
    def visual(self, kind, magnitudes):
        return NewspaperVisual(
            kind=kind, title="A complete fictional comparison",
            caption="Complete labels preserve conditions instead of losing their final words.",
            items=[NewspaperVisualItem(
                value=f"{value}%", label=f"Fictional segment {index}",
                detail=f"Condition {index} remains subject to independent approval.",
                magnitude=value,
            ) for index, value in enumerate(magnitudes)], source_urls=[],
        )

    def test_six_visual_items_keep_complete_copy_and_readable_type(self):
        import pymupdf as fitz
        from reportlab.pdfgen.canvas import Canvas

        renderer = NewspaperRenderer(load_settings("config.example.toml"))
        for kind in ("stat_grid", "comparison", "timeline", "process", "news_grid", "bar_chart"):
            with self.subTest(kind=kind):
                visual = self.visual(kind, range(6))
                plan = renderer._visual_plan(visual, 535)
                buffer = BytesIO()
                canvas = Canvas(buffer)
                renderer._draw_visual(canvas, visual, x=30, top=760, width=535,
                                      height=plan["height"], dark=True)
                canvas.save()
                with fitz.open(stream=buffer.getvalue(), filetype="pdf") as document:
                    page = document[0]
                    text = " ".join(page.get_text().split())
                    self.assertIn(visual.caption, text)
                    for item in visual.items:
                        self.assertIn(item.label, text)
                        self.assertIn(item.detail, text)
                    spans = [span for block in page.get_text("dict")["blocks"]
                             for line in block.get("lines", []) for span in line["spans"]]
                    self.assertTrue(all(span["size"] >= 6.1 for span in spans))
                    self.assertTrue(all(span["color"] != 0 for span in spans))
                    self.assertTrue(all(30 <= span["bbox"][0] <= span["bbox"][2] <= 565.1
                                        for span in spans))

    def test_signed_chart_shares_zero_and_zero_never_gets_a_filled_bar(self):
        from reportlab.pdfgen.canvas import Canvas

        renderer = NewspaperRenderer(load_settings("config.example.toml"))
        for magnitudes, expected in (([-10, 0, 20], 5), ([0, 0], 2)):
            visual = self.visual("bar_chart", magnitudes)
            canvas = Canvas(BytesIO())
            with patch.object(canvas, "rect", wraps=canvas.rect) as rectangles:
                renderer._draw_visual(canvas, visual, x=30, top=760, width=535,
                                      height=renderer._visual_plan(visual, 535)["height"],
                                      dark=True)
            self.assertEqual(expected, rectangles.call_count)
            if magnitudes[0] < 0:
                track, negative, _zero_track, _positive_track, positive = [
                    call.args for call in rectangles.call_args_list
                ]
                origin = track[0] + track[2] / 2
                self.assertAlmostEqual(origin, negative[0] + negative[2])
                self.assertAlmostEqual(origin, positive[0])
                self.assertAlmostEqual(negative[2] * 2, positive[2])

    def test_magnitudes_reject_boolean_nonfinite_and_overflow_values(self):
        for value in (True, float("nan"), float("inf"), -float("inf"), 10 ** 400):
            with self.subTest(value=str(value)[:20]), self.assertRaises(DataValidationError):
                NewspaperVisualItem.from_dict({"label": "A label", "value": "A value",
                                               "magnitude": value})

    def test_unfittable_visual_and_briefs_fail_instead_of_clipping(self):
        from reportlab.pdfgen.canvas import Canvas

        renderer = NewspaperRenderer(load_settings("config.example.toml"))
        visual = self.visual("news_grid", [1, 2])
        with self.assertRaises(NewspaperRenderError):
            renderer._draw_visual(Canvas(BytesIO()), visual, x=30, top=760,
                                  width=535, height=20, dark=False)
        issue = NewspaperIssue.from_dict({
            "headline": "A headline", "deck": "A deck", "lead": "A lead",
            "articles": [{"title": "A story", "body": "A complete distinct fact."}],
            "briefs": ["A complete independent secondary development remains documented."],
        })
        with self.assertRaises(NewspaperRenderError):
            renderer._draw_briefs(Canvas(BytesIO()), issue, x=30, top=760,
                                  width=100, height=30)

    def test_overlap_preserves_changed_figures_conditions_and_actor_order(self):
        original = "The operator approved a 12% increase only if a regulator agreed."
        variants = ["The operator approved a 15% increase only if a regulator agreed.",
                    "The operator approved a 12% increase before a regulator agreed."]
        for text in variants:
            self.assertTrue(_bullet_adds_distinct_information(text, original))
            self.assertFalse(_bullet_repeats_article(text, original))
        bodies = [original, *variants, "Company A acquired Company B.",
                  "Company B acquired Company A.", original]
        issue = SimpleNamespace(articles=[
            NewspaperArticle(str(index), body, [], [], story_ids=[str(index)])
            for index, body in enumerate(bodies)
        ])
        _deduplicate_newspaper_articles(issue, set())
        self.assertEqual(bodies[:-1], [article.body for article in issue.articles])

    def test_reader_briefs_never_copy_articles_as_fallback(self):
        article = NewspaperArticle("A headline", "A distinct reported change.", [], [])
        issue = SimpleNamespace(articles=[article], briefs=[], data_points=[])
        self.assertEqual([], NewspaperRenderer._reader_briefs(issue))
        issue.data_points = [article.body, "A distinct secondary result remained unchanged."]
        self.assertEqual([issue.data_points[1]], NewspaperRenderer._reader_briefs(issue))
