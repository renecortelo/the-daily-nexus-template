"""Source-passage lineage for the existing editorial calls, never another review pass."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from audiodigest.content import minimize_editorial_text, normalize_url
from audiodigest.models import DataValidationError, SourceItem, Story, StoryEvidence

MAX_PASSAGE_CHARS = 900
MAX_SUPPORT_CHARS = 3 * MAX_PASSAGE_CHARS + 2
MAX_REVIEW_EVIDENCE_CHARS = 120_000
MAX_FACT_SUPPORTS = 2


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _passages(text: str) -> list[str]:
    """Keep ordinary source words, splitting long lines at word boundaries.

    Excerpts are whitespace-normalized source text, not new summaries. A passage
    boundary is not proof that the surrounding context can be ignored.
    """
    result: list[str] = []
    for line in text.splitlines():
        words = line.split()
        part: list[str] = []
        length = 0
        for word in words:
            if len(word) > MAX_PASSAGE_CHARS:
                # Opaque blobs cannot be useful spoken evidence. Do not let one
                # tracking/image token discard all other newsletter reporting.
                word = "[oversized-source-token-omitted]"
            if part and length + 1 + len(word) > MAX_PASSAGE_CHARS:
                result.append(" ".join(part))
                part, length = [], 0
            part.append(word)
            length += len(word) + (1 if len(part) > 1 else 0)
        if part:
            result.append(" ".join(part))
    return result


@dataclass(frozen=True, slots=True)
class SourcePassage:
    source_id: str
    source_alias: str
    passage_id: str
    source_type: str
    kind: str
    publication: str
    url: str
    excerpt: str

    def support(self, index: int) -> StoryEvidence:
        return StoryEvidence(
            fact_index=index, source_id=self.source_id, passage_id=self.passage_id,
            source_type=self.source_type, kind=self.kind, publication=self.publication,
            url=self.url, excerpt=self.excerpt, excerpt_sha256=_digest(self.excerpt),
        )


class SourceEvidenceIndex:
    def __init__(self, sources: list[SourceItem], prompt_sources: list[dict[str, Any]]):
        self.passages: dict[str, SourcePassage] = {}
        self.allowed_urls: dict[str, set[str]] = {}
        self.prompts: list[dict[str, Any]] = []
        for source, supplied in zip(sources, prompt_sources, strict=True):
            prompt = dict(supplied)
            alias = prompt["message_id"]
            urls = set(prompt["source_urls"])
            prompt["newsletter_passages"] = self._register(
                source, alias, "mail", prompt.pop("email_text"), prompt["publication"], "",
            )
            articles = []
            for index, supplied_article in enumerate(prompt["articles"], start=1):
                article = dict(supplied_article)
                urls.update(url for url in (article["url"], article["canonical_url"]) if url)
                article["passages"] = self._register(
                    source, alias, f"article-{index:03d}", article.pop("text"),
                    prompt["publication"], article["canonical_url"] or article["url"],
                )
                articles.append(article)
            prompt["articles"] = articles
            self.allowed_urls[alias] = urls
            self.prompts.append(prompt)

    def _register(self, source, alias, group, text, publication, url):
        records = []
        parts = _passages(text)
        for index, text_part in enumerate(parts, start=1):
            ref = f"{alias}:{group}:{index:03d}"
            # Keep adjacent source context (including qualifications/negation),
            # not only the sentence the extracting model elected to cite.
            excerpt = " ".join(parts[max(0, index - 2):index + 1])
            self.passages[ref] = SourcePassage(
                source.message_id, alias, ref, source.source_type,
                "body" if group == "mail" else "article", publication, url, excerpt,
            )
            records.append({"id": ref, "text": text_part})
        return records

    def bind(self, story: Story, raw: dict[str, Any]) -> None:
        """Resolve supplied passage IDs locally before deduplication or drafting."""
        cited_sources = set(story.source_ids)
        if not cited_sources.issubset(self.allowed_urls):
            raise DataValidationError("extracted story uses an unknown source alias")
        allowed = set().union(*(self.allowed_urls[alias] for alias in cited_sources))
        urls = []
        for value in story.source_urls:
            safe = normalize_url(value)
            if not safe or safe not in allowed:
                raise DataValidationError("story URL does not belong to its cited source records")
            if safe not in urls:
                urls.append(safe)
        refs = raw.get("evidence_refs")
        if not isinstance(refs, list) or len(refs) != len(story.facts):
            raise DataValidationError("every extracted fact needs a source-passage reference")
        support = []
        supported_sources: set[str] = set()
        for index, references in enumerate(refs):
            if not isinstance(references, list) or not 1 <= len(references) <= MAX_FACT_SUPPORTS:
                raise DataValidationError("each fact needs one or two source-passage references")
            for ref in references:
                passage = self.passages.get(ref) if isinstance(ref, str) else None
                if passage is None or passage.source_alias not in cited_sources:
                    raise DataValidationError(
                        "fact refers to an unknown or unrelated source passage"
                    )
                supported_sources.add(passage.source_alias)
                item = passage.support(index)
                if item not in support:
                    support.append(item)
        if supported_sources != cited_sources:
            raise DataValidationError("every cited source must support at least one extracted fact")
        story.source_urls = urls
        story.evidence = support

    def check(self, support: StoryEvidence) -> None:
        passage = self.passages.get(support.passage_id)
        if passage is None or passage.support(support.fact_index) != support:
            raise DataValidationError("story evidence differs from the collected source passage")


def review_evidence(
    stories: list[Story], aliases: dict[str, str], index: SourceEvidenceIndex | None,
) -> dict[str, Any]:
    """A compact, deduplicated packet of actual source text for factual review.

    Legacy stories without original excerpts fail closed. Saved excerpt hashes
    detect corruption, not authorship or authenticity against a malicious editor.
    """
    passages: dict[str, dict[str, Any]] = {}
    claims = []
    for story in stories:
        if index is not None:
            source_aliases = {aliases[source] for source in story.source_ids}
            if not source_aliases.issubset(index.allowed_urls):
                raise DataValidationError("story source is not part of the collected evidence")
            allowed = set().union(*(index.allowed_urls[alias] for alias in source_aliases))
            if any(not normalize_url(url) or normalize_url(url) not in allowed
                   for url in story.source_urls):
                raise DataValidationError("story URL differs from its collected source records")
        supports: dict[int, list[str]] = {i: [] for i in range(len(story.facts))}
        for support in story.evidence:
            if (isinstance(support.fact_index, bool) or support.fact_index not in supports
                    or support.source_id not in story.source_ids):
                raise DataValidationError(
                    "story evidence has an invalid fact or source association"
                )
            if not support.excerpt or len(support.excerpt) > MAX_SUPPORT_CHARS:
                raise DataValidationError("story evidence excerpt is missing or oversized")
            if support.excerpt_sha256 != _digest(support.excerpt):
                raise DataValidationError("saved source excerpt integrity check failed")
            if index is not None:
                index.check(support)
            alias = aliases[support.source_id]
            ref = f"{alias}:{support.passage_id.partition(':')[2]}"
            if not ref.partition(':')[2]:
                raise DataValidationError("saved source passage identifier is invalid")
            record = {
                "id": ref, "source_id": alias, "publication": support.publication,
                "source_type": support.source_type, "kind": support.kind,
                "url": normalize_url(support.url) if support.url else "",
                "text": minimize_editorial_text(support.excerpt),
            }
            if ref in passages and passages[ref] != record:
                raise DataValidationError("source passage identifier has conflicting evidence")
            passages[ref] = record
            if ref not in supports[support.fact_index]:
                supports[support.fact_index].append(ref)
        if not supports or any(not refs for refs in supports.values()):
            raise DataValidationError(
                "original source passages are missing; recollect newsletters before factual review"
            )
        claims.extend(
            {"story_id": story.story_id, "fact_index": i, "passage_ids": refs}
            for i, refs in supports.items()
        )
    if sum(len(item["text"]) for item in passages.values()) > MAX_REVIEW_EVIDENCE_CHARS:
        raise DataValidationError("original-source evidence exceeds the bounded review size")
    return {
        "version": "source-passages-v1",
        "origin": "collected-this-run" if index is not None else "saved-source-excerpts",
        "passages": list(passages.values()), "fact_support": claims,
    }
