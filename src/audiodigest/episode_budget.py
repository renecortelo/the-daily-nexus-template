"""Evidence-sized episodes: useful coverage, never minutes filled with repetition."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from audiodigest.constants import Section
from audiodigest.models import Story

MAX_EPISODE_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class EpisodeBudget:
    newsletter_count: int
    available_stories: int
    unique_facts: int
    selected_stories: int
    selected_news_stories: int
    represented_newsletters: int
    suggested_min_words: int
    max_words: int
    estimated_max_minutes: float
    selected_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "policy": "evidence-sized-v1",
            "hard_max_seconds": MAX_EPISODE_SECONDS,
        }


def plan_episode(
    stories: list[Story], app, newsletter_ids: list[str] | None = None
) -> EpisodeBudget:
    """A newsletter ceiling is reduced by distinct evidence, including duplicate facts.

    Source-balanced selection gives one story per represented newsletter before
    adding more from an already represented source. The newspaper keeps the full
    story set. Neither research nor many empty newsletters inflate the budget.
    """
    history = [story for story in stories if story.section == Section.TODAY_IN_HISTORY]
    news = [story for story in stories if story.section != Section.TODAY_IN_HISTORY]
    source_ids = (
        set(newsletter_ids)
        if newsletter_ids is not None
        else {source for story in news for source in story.source_ids}
    )
    if newsletter_ids is not None:
        news = [story for story in news if source_ids.intersection(story.source_ids)]
    count = len(source_ids)
    ceiling = (
        600
        if count <= 1
        else 1300
        if count <= 3
        else 2200
        if count <= 7
        else 3000
        if count <= 15
        else 3400
    )
    facts = {
        normalized
        for story in news
        for fact in story.facts
        if (normalized := re.sub(r"\W+", " ", fact.casefold()).strip())
    }
    # One fact can need context, but cannot justify a long episode by itself.
    evidence_ceiling = 230 + 55 * len(facts) + 12 * len(news) + (120 if history else 0)
    maximum = min(int(app.target_max_words), ceiling, max(260, evidence_ceiling))
    # Rich broad editions keep their depth; sparse editions are deliberately brief.
    # This remains guidance, never a failing minimum or an expansion trigger.
    minimum = min(int(app.target_min_words), round(maximum * (0.85 if count >= 8 else 0.60)))
    history = history[:2]
    news_limit = max(1, min(30 - len(history), (maximum - (200 if history else 140)) // 55))
    ranked = sorted(news, key=lambda story: (story.rank_score, story.confidence), reverse=True)
    selected: list[Story] = []
    covered: set[str] = set()
    for story in ranked:
        if len(selected) >= news_limit:
            break
        if set(story.source_ids).intersection(source_ids) - covered:
            selected.append(story)
            covered.update(set(story.source_ids).intersection(source_ids))
    for story in ranked:
        if len(selected) >= news_limit:
            break
        if story not in selected:
            selected.append(story)
            covered.update(set(story.source_ids).intersection(source_ids))
    selected_news_count = len(selected)
    selected = history + selected
    return EpisodeBudget(
        newsletter_count=count,
        available_stories=len(news),
        unique_facts=len(facts),
        selected_stories=len(selected),
        selected_news_stories=selected_news_count,
        represented_newsletters=len(covered),
        suggested_min_words=minimum,
        max_words=maximum,
        estimated_max_minutes=round(maximum / 135 + 0.5, 1),
        selected_ids=tuple(story.story_id for story in selected),
    )
