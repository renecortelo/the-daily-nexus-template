from types import SimpleNamespace
from unittest import TestCase

from audiodigest.episode_budget import MAX_EPISODE_SECONDS, plan_episode
from audiodigest.models import Story


def news(index, source, *, facts=8, section="AI", rank=10):
    return Story.from_dict(
        {
            "story_id": f"story-{index}",
            "section": section,
            "headline": f"Synthetic development {index}",
            "facts": [f"Verified detail {index}-{number}." for number in range(facts)],
            "why_it_matters": "A supported operational change.",
            "source_ids": [source],
            "source_urls": [],
            "confidence": 0.9,
            "rank_score": rank,
        }
    )


class EpisodeBudgetTests(TestCase):
    app = SimpleNamespace(target_min_words=2850, target_max_words=3800)

    def test_newsletter_bands_are_ceilings_not_minimum_durations(self):
        for count, ceiling in [(1, 600), (3, 1300), (7, 2200), (15, 3000), (25, 3400)]:
            stories = [news(index, f"source-{index}", facts=100) for index in range(count)]
            budget = plan_episode(stories, self.app)
            self.assertEqual(ceiling, budget.max_words)
            self.assertLess(budget.suggested_min_words, budget.max_words)
            self.assertLess(budget.estimated_max_minutes, 27)
        self.assertEqual(1800, MAX_EPISODE_SECONDS)

    def test_rich_broad_editions_keep_a_substantial_soft_guide(self):
        stories = [news(index, f"source-{index}", facts=100) for index in range(25)]
        budget = plan_episode(stories, self.app)
        self.assertEqual(2850, budget.suggested_min_words)

    def test_many_empty_or_repeated_newsletters_do_not_force_long_audio(self):
        stories = [news(0, f"source-{index}", facts=1) for index in range(25)]
        budget = plan_episode(stories, self.app)
        self.assertEqual(1, budget.unique_facts)
        self.assertLess(budget.max_words, 600)

    def test_research_never_inflates_newsletter_count_or_news_shortlist(self):
        history = news(99, "research", section="TIH: Today in History")
        budget = plan_episode([news(0, "email"), news(1, "research"), history], self.app, ["email"])
        self.assertEqual(1, budget.newsletter_count)
        self.assertEqual(1, budget.available_stories)
        self.assertNotIn("story-1", budget.selected_ids)
        self.assertIn("story-99", budget.selected_ids)
        self.assertLessEqual(budget.max_words, 600)

    def test_shortlist_represents_sources_before_adding_more_from_one_source(self):
        stories = [news(index, "busy", rank=100 - index) for index in range(20)]
        stories += [news(90, "quiet-a", rank=1), news(91, "quiet-b", rank=1)]
        budget = plan_episode(stories, self.app)
        self.assertIn("story-90", budget.selected_ids)
        self.assertIn("story-91", budget.selected_ids)
        self.assertEqual(3, budget.represented_newsletters)
        self.assertLess(budget.selected_stories, len(stories))

    def test_history_and_news_together_fit_structural_coverage_limit(self):
        stories = [news(index, f"source-{index}") for index in range(60)]
        stories += [news(100, "research", section="TIH: Today in History")]
        budget = plan_episode(stories, self.app)
        self.assertEqual(30, budget.selected_stories)
        self.assertEqual(29, budget.selected_news_stories)

    def test_configured_ceiling_is_respected(self):
        budget = plan_episode(
            [news(0, "email", facts=100)],
            SimpleNamespace(target_min_words=400, target_max_words=500),
        )
        self.assertEqual(500, budget.max_words)
