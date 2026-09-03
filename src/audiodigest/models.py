from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from audiodigest.constants import (
    AI_DISCLOSURE,
    DEFAULT_SECTION_NAMES,
    SectionReference,
    parse_section,
)


class DataValidationError(ValueError):
    """Raised when model or persisted data does not match the required contract."""


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{key!r} must be a non-empty string")
    return value.strip()


def _string_list(data: dict[str, Any], key: str, *, minimum: int = 0) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DataValidationError(f"{key!r} must be a list of strings")
    result = [item.strip() for item in value if item.strip()]
    if len(result) < minimum:
        raise DataValidationError(f"{key!r} must contain at least {minimum} item(s)")
    return result


@dataclass(slots=True)
class DialogueTurn:
    host: str
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DialogueTurn:
        return cls(
            host=_required_str(data, "host"),
            text=_required_str(data, "text"),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _dialogue_list(
    data: dict[str, Any],
    key: str,
    *,
    default_host: str,
) -> list[DialogueTurn]:
    value = data.get(key)
    if isinstance(value, str):
        if not value.strip():
            raise DataValidationError(f"{key!r} must not be empty")
        return [DialogueTurn(host=default_host, text=value.strip())]
    if not isinstance(value, list) or not value:
        raise DataValidationError(f"{key!r} must be a non-empty dialogue list")
    if any(not isinstance(item, dict) for item in value):
        raise DataValidationError(f"{key!r} dialogue entries must be objects")
    return [DialogueTurn.from_dict(item) for item in value]


@dataclass(slots=True)
class ArticleReference:
    url: str
    canonical_url: str
    title: str
    text: str
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceItem:
    message_id: str
    publication: str
    sender: str
    subject: str
    received_at: datetime
    email_text: str
    source_type: str = "newsletter"
    article_urls: list[str] = field(default_factory=list)
    articles: list[ArticleReference] = field(default_factory=list)
    link_stats: dict[str, int] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "publication": self.publication,
            "source_type": self.source_type,
            "subject": self.subject,
            "received_at": self.received_at.isoformat(),
            "email_text": self.email_text[:50_000],
            "source_urls": self.article_urls,
            "articles": [
                {
                    **article.to_dict(),
                    "text": article.text[:50_000],
                }
                for article in self.articles
            ],
        }


@dataclass(slots=True)
class Story:
    story_id: str
    section: SectionReference
    headline: str
    facts: list[str]
    why_it_matters: str
    source_ids: list[str]
    source_urls: list[str]
    confidence: float
    rank_score: float

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        allowed_sections: Sequence[str] | None = DEFAULT_SECTION_NAMES,
    ) -> Story:
        try:
            section = parse_section(
                _required_str(data, "section"),
                allowed_sections=allowed_sections,
            )
        except ValueError as exc:
            raise DataValidationError(str(exc)) from exc
        confidence = data.get("confidence")
        rank_score = data.get("rank_score")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise DataValidationError("confidence must be a number from 0 to 1")
        if not isinstance(rank_score, (int, float)):
            raise DataValidationError("rank_score must be numeric")
        return cls(
            story_id=_required_str(data, "story_id"),
            section=section,
            headline=_required_str(data, "headline"),
            facts=_string_list(data, "facts", minimum=1),
            why_it_matters=_required_str(data, "why_it_matters"),
            source_ids=_string_list(data, "source_ids", minimum=1),
            source_urls=_string_list(data, "source_urls"),
            confidence=float(confidence),
            rank_score=float(rank_score),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["section"] = self.section.value
        return result


@dataclass(slots=True)
class ScriptSection:
    name: SectionReference
    dialogue: list[DialogueTurn]
    story_ids: list[str]

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        default_host: str = "Dalia",
        allowed_sections: Sequence[str] | None = DEFAULT_SECTION_NAMES,
    ) -> ScriptSection:
        try:
            name = parse_section(
                _required_str(data, "name"),
                allowed_sections=allowed_sections,
            )
        except ValueError as exc:
            raise DataValidationError(str(exc)) from exc
        dialogue_key = "dialogue" if "dialogue" in data else "narration"
        return cls(
            name=name,
            dialogue=_dialogue_list(data, dialogue_key, default_host=default_host),
            story_ids=_string_list(data, "story_ids", minimum=1),
        )

    @property
    def narration(self) -> str:
        return " ".join(turn.text for turn in self.dialogue)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "dialogue": [turn.to_dict() for turn in self.dialogue],
            "story_ids": self.story_ids,
        }


@dataclass(slots=True)
class EpisodeScript:
    title: str
    hosts: list[str]
    introduction: list[DialogueTurn]
    sections: list[ScriptSection]
    conclusion: list[DialogueTurn]
    sign_off: list[DialogueTurn]
    show_notes: list[str]
    disclosure: str = AI_DISCLOSURE

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        section_order: Sequence[str] | None = DEFAULT_SECTION_NAMES,
    ) -> EpisodeScript:
        hosts = _string_list(data, "hosts") or ["Dalia"]
        if len(hosts) not in {1, 2}:
            raise DataValidationError("an episode must have one or two hosts")
        if len({host.casefold() for host in hosts}) != len(hosts):
            raise DataValidationError("host names must be unique")
        default_host = hosts[0]
        raw_sections = data.get("sections")
        if not isinstance(raw_sections, list):
            raise DataValidationError("sections must be a list")
        sections = [
            ScriptSection.from_dict(
                item,
                default_host=default_host,
                allowed_sections=section_order,
            )
            for item in raw_sections
        ]
        if section_order is not None:
            order = {section: index for index, section in enumerate(section_order)}
            indexes = [order[item.name.value] for item in sections]
            if indexes != sorted(indexes):
                raise DataValidationError("sections are not in the required order")
        if len({item.name for item in sections}) != len(sections):
            raise DataValidationError("script contains duplicate sections")
        script = cls(
            title=_required_str(data, "title"),
            hosts=hosts,
            introduction=_dialogue_list(
                data,
                "introduction",
                default_host=default_host,
            ),
            sections=sections,
            conclusion=_dialogue_list(
                data,
                "conclusion",
                default_host=default_host,
            ),
            sign_off=_dialogue_list(
                data,
                "sign_off",
                default_host=default_host,
            ),
            show_notes=_string_list(data, "show_notes"),
            disclosure=str(data.get("disclosure") or AI_DISCLOSURE).strip(),
        )
        allowed_hosts = {host.casefold() for host in hosts}
        for turn in script.dialogue_turns:
            if turn.host.casefold() not in allowed_hosts:
                raise DataValidationError(
                    f"dialogue host {turn.host!r} is not in the configured host list"
                )
        return script

    @property
    def dialogue_turns(self) -> list[DialogueTurn]:
        result = [*self.introduction]
        for section in self.sections:
            result.extend(section.dialogue)
        result.extend(self.conclusion)
        result.extend(self.sign_off)
        return result

    @property
    def introduction_text(self) -> str:
        return " ".join(turn.text for turn in self.introduction)

    @property
    def conclusion_text(self) -> str:
        return " ".join(turn.text for turn in self.conclusion)

    @property
    def sign_off_text(self) -> str:
        return " ".join(turn.text for turn in self.sign_off)

    @property
    def transcript(self) -> str:
        parts = [f"{turn.host}: {turn.text}" for turn in self.introduction]
        for section in self.sections:
            parts.append(section.name.value)
            parts.extend(f"{turn.host}: {turn.text}" for turn in section.dialogue)
        parts.extend(f"{turn.host}: {turn.text}" for turn in self.conclusion)
        parts.extend(f"{turn.host}: {turn.text}" for turn in self.sign_off)
        return "\n\n".join(parts)

    @property
    def narration(self) -> str:
        parts = [self.disclosure, self.introduction_text]
        for section in self.sections:
            parts.extend([section.name.value, section.narration])
        parts.extend([self.conclusion_text, self.sign_off_text])
        return "\n\n".join(part for part in parts if part.strip())

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "hosts": self.hosts,
            "introduction": [turn.to_dict() for turn in self.introduction],
            "sections": [item.to_dict() for item in self.sections],
            "conclusion": [turn.to_dict() for turn in self.conclusion],
            "sign_off": [turn.to_dict() for turn in self.sign_off],
            "show_notes": self.show_notes,
            "disclosure": self.disclosure,
            "word_count": self.word_count,
        }


@dataclass(slots=True)
class NewspaperArticle:
    title: str
    body: str
    source_urls: list[str]
    bullet_points: list[str]
    section_label: str = "Briefing"
    standfirst: str = ""
    story_ids: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewspaperArticle:
        return cls(
            title=_required_str(data, "title"),
            body=_required_str(data, "body"),
            source_urls=_string_list(data, "source_urls"),
            bullet_points=_string_list(data, "bullet_points"),
            section_label=str(data.get("section_label") or "Briefing").strip(),
            standfirst=str(data.get("standfirst") or "").strip(),
            story_ids=_string_list(data, "story_ids"),
            highlights=_string_list(data, "highlights"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NewspaperBrief:
    text: str
    story_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: Any) -> NewspaperBrief:
        # Editions saved before structured coverage metadata used plain strings.
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise DataValidationError("newspaper brief text must not be empty")
            return cls(text=text)
        if not isinstance(value, dict):
            raise DataValidationError("newspaper briefs must be strings or objects")
        return cls(
            text=_required_str(value, "text"),
            story_ids=_string_list(value, "story_ids"),
            source_urls=_string_list(value, "source_urls"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NewspaperVisualItem:
    label: str
    value: str
    detail: str = ""
    magnitude: float | None = None
    story_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewspaperVisualItem:
        magnitude = data.get("magnitude")
        if magnitude is not None and not isinstance(magnitude, (int, float)):
            raise DataValidationError("visual item magnitude must be numeric or null")
        return cls(
            label=_required_str(data, "label"),
            value=_required_str(data, "value"),
            detail=str(data.get("detail") or "").strip(),
            magnitude=float(magnitude) if magnitude is not None else None,
            story_ids=_string_list(data, "story_ids"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NewspaperVisual:
    kind: str
    title: str
    caption: str
    items: list[NewspaperVisualItem]
    source_urls: list[str]

    ALLOWED_KINDS = frozenset(
        {
            "stat_grid",
            "timeline",
            "comparison",
            "bar_chart",
            "process",
            "signal_map",
            "decision_matrix",
            "news_grid",
        }
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewspaperVisual:
        kind = _required_str(data, "kind")
        if kind not in cls.ALLOWED_KINDS:
            allowed = ", ".join(sorted(cls.ALLOWED_KINDS))
            raise DataValidationError(f"visual kind must be one of: {allowed}")
        raw_items = data.get("items")
        if (
            not isinstance(raw_items, list)
            or not 2 <= len(raw_items) <= 6
            or any(not isinstance(item, dict) for item in raw_items)
        ):
            raise DataValidationError("visual items must contain 2 to 6 objects")
        return cls(
            kind=kind,
            title=_required_str(data, "title"),
            caption=str(data.get("caption") or "").strip(),
            items=[NewspaperVisualItem.from_dict(item) for item in raw_items],
            source_urls=_string_list(data, "source_urls"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "caption": self.caption,
            "items": [item.to_dict() for item in self.items],
            "source_urls": self.source_urls,
        }


@dataclass(slots=True)
class NewspaperIssue:
    headline: str
    deck: str
    lead: str
    articles: list[NewspaperArticle]
    data_points: list[str]
    sources: list[str]
    kicker: str = "Morning briefing"
    pull_quote: str = ""
    briefs: list[NewspaperBrief] = field(default_factory=list)
    executive_summary: list[NewspaperVisualItem] = field(default_factory=list)
    visuals: list[NewspaperVisual] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewspaperIssue:
        raw_articles = data.get("articles")
        if (
            not isinstance(raw_articles, list)
            or not 1 <= len(raw_articles) <= 8
            or any(not isinstance(item, dict) for item in raw_articles)
        ):
            raise DataValidationError("newspaper articles must contain 1 to 8 objects")
        raw_visuals = data.get("visuals", [])
        if (
            not isinstance(raw_visuals, list)
            or len(raw_visuals) > 3
            or any(not isinstance(item, dict) for item in raw_visuals)
        ):
            raise DataValidationError("newspaper visuals must contain up to 3 objects")
        raw_executive_summary = data.get("executive_summary", [])
        if (
            not isinstance(raw_executive_summary, list)
            or len(raw_executive_summary) > 4
            or any(not isinstance(item, dict) for item in raw_executive_summary)
        ):
            raise DataValidationError(
                "newspaper executive_summary must contain up to 4 objects"
            )
        raw_briefs = data.get("briefs", [])
        if (
            not isinstance(raw_briefs, list)
            or len(raw_briefs) > 8
            or any(not isinstance(item, (str, dict)) for item in raw_briefs)
        ):
            raise DataValidationError(
                "newspaper briefs must contain up to 8 strings or objects"
            )
        issue = cls(
            headline=_required_str(data, "headline"),
            deck=_required_str(data, "deck"),
            lead=_required_str(data, "lead"),
            articles=[NewspaperArticle.from_dict(item) for item in raw_articles],
            data_points=_string_list(data, "data_points"),
            sources=_string_list(data, "sources"),
            kicker=str(data.get("kicker") or "Morning briefing").strip(),
            pull_quote=str(data.get("pull_quote") or "").strip(),
            briefs=[NewspaperBrief.from_value(item) for item in raw_briefs],
            executive_summary=[
                NewspaperVisualItem.from_dict(item) for item in raw_executive_summary
            ],
            visuals=[NewspaperVisual.from_dict(item) for item in raw_visuals],
        )
        if issue.word_count > 1_600:
            raise DataValidationError(
                "newspaper issue exceeds the 1,600-word readable-layout limit"
            )
        return issue

    @property
    def word_count(self) -> int:
        text = " ".join(
            [
                self.headline,
                self.deck,
                self.lead,
                *(
                    (
                        f"{article.section_label} {article.title} {article.standfirst} "
                        f"{article.body} {' '.join(article.bullet_points)}"
                    )
                    for article in self.articles
                ),
                *self.data_points,
                self.kicker,
                self.pull_quote,
                *(brief.text for brief in self.briefs),
                *(
                    f"{item.value} {item.label} {item.detail}"
                    for item in self.executive_summary
                ),
                *(
                    " ".join(
                        [
                            visual.title,
                            visual.caption,
                            *(
                                f"{item.value} {item.label} {item.detail}"
                                for item in visual.items
                            ),
                        ]
                    )
                    for visual in self.visuals
                ),
            ]
        )
        return len(text.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "deck": self.deck,
            "lead": self.lead,
            "articles": [article.to_dict() for article in self.articles],
            "data_points": self.data_points,
            "sources": self.sources,
            "kicker": self.kicker,
            "pull_quote": self.pull_quote,
            "briefs": [brief.to_dict() for brief in self.briefs],
            "executive_summary": [
                item.to_dict() for item in self.executive_summary
            ],
            "visuals": [visual.to_dict() for visual in self.visuals],
            "word_count": self.word_count,
        }


@dataclass(slots=True)
class VerificationResult:
    approved: bool
    issues: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        approved = data.get("approved")
        if not isinstance(approved, bool):
            raise DataValidationError("approved must be a boolean")
        issues = _string_list(data, "issues")
        if not approved and not issues:
            raise DataValidationError("a rejected script must contain at least one issue")
        return cls(approved=approved, issues=issues)


@dataclass(slots=True)
class AntigravityMetadata:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
