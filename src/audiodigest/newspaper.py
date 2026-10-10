from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from audiodigest.config import Settings
from audiodigest.models import (
    EpisodeScript,
    NewspaperArticle,
    NewspaperIssue,
    NewspaperVisual,
    NewspaperVisualItem,
)


class NewspaperRenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NewspaperResult:
    pdf_path: Path
    preview_paths: tuple[Path, ...]

    @property
    def preview_path(self) -> Path:
        return self.preview_paths[0]


def _percent_symbols(text: str) -> str:
    return re.sub(r"\s+percent\b", "%", text, flags=re.IGNORECASE)


def _complete_sentences(text: str) -> list[str]:
    normalized = _percent_symbols(" ".join(text.split()))
    protected = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0).replace(".", "\u2024"),
        normalized,
    )
    protected = re.sub(
        r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.",
        lambda match: match.group(0).replace(".", "\u2024"),
        protected,
    )
    return [
        sentence.replace("\u2024", ".").strip()
        for sentence in re.split(r"(?<=[.!?])\s+", protected)
        if sentence.strip()
    ]


def _bullet_adds_distinct_information(
    bullet: str,
    article_text: str,
) -> bool:
    key = _reader_copy_key(bullet)
    return not any(
        key == _reader_copy_key(sentence) for sentence in _complete_sentences(article_text)
    )


def _reader_copy_key(text: str) -> str:
    """Exact normalized copy only; overlap is not proof of redundant meaning."""
    return _percent_symbols(" ".join(text.split())).casefold().rstrip(".!? ")


def _source_domains(issue: NewspaperIssue) -> list[str]:
    domains: list[str] = []
    values = [
        *(url for article in issue.articles for url in article.source_urls),
        *(url for visual in issue.visuals for url in visual.source_urls),
        *(url for source in issue.sources for url in re.findall(r"https://[^\s]+", source)),
    ]
    for url in values:
        hostname = (urlsplit(url.rstrip(".,);]")).hostname or "").removeprefix("www.")
        if hostname and hostname not in domains:
            domains.append(hostname)
    newsletter_domains = [item for item in domains if "wikipedia.org" not in item]
    return sorted(newsletter_domains or domains)


def _limited_words(text: str, limit: int) -> str:
    normalized = _percent_symbols(" ".join(text.split()))
    words = normalized.split()
    if len(words) <= limit:
        return normalized

    protected = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0).replace(".", "\u2024"),
        normalized,
    )
    protected = re.sub(
        r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.",
        lambda match: match.group(0).replace(".", "\u2024"),
        protected,
    )
    sentences = [
        sentence.replace("\u2024", ".") for sentence in re.split(r"(?<=[.!?])\s+", protected)
    ]
    selected: list[str] = []
    word_count = 0
    for sentence in sentences:
        sentence_words = sentence.split()
        if selected and word_count + len(sentence_words) > limit:
            break
        if not selected and len(sentence_words) > limit:
            # A synthetic full stop can remove a necessary condition. Preserve
            # the complete sentence; layout fitting, not slicing, owns overflow.
            return sentence.strip()
        selected.append(sentence)
        word_count += len(sentence_words)
    return " ".join(selected).strip()


def _short_visual_text(text: str, *, words: int, characters: int) -> str:
    """Shorten display-only visual copy without slicing a word in half.

    Visuals should never look like a browser clipped their copy.  New editions
    are validated to fit these limits; this guard mainly keeps older saved
    editions legible and makes any intentional shortening explicit.
    """

    normalized = _percent_symbols(" ".join(text.split()))
    selected: list[str] = []
    for word in normalized.split():
        candidate = " ".join([*selected, word])
        if selected and (len(selected) >= words or len(candidate) > characters):
            break
        selected.append(word)
    if not selected:
        return normalized
    result = " ".join(selected)
    if len(selected) < len(normalized.split()):
        return result.rstrip(" ,;:-") + "..."
    return result


def _news_visual_copy(article: NewspaperArticle) -> tuple[str, str]:
    protected = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0).replace(".", "\u2024"),
        " ".join(article.body.split()),
    )
    sentences = [
        item.replace("\u2024", ".").strip()
        for item in re.split(r"(?<=[.!?])\s+", protected)
        if item.strip()
    ]
    generic_starts = (
        "we begin with",
        "this edition",
        "this section",
        "in today's episode",
    )
    useful = [
        sentence for sentence in sentences if not sentence.casefold().startswith(generic_starts)
    ]
    article_title_is_desk = article.title.casefold() == article.section_label.casefold()
    headline = useful[0] if article_title_is_desk and useful else article.title
    detail_candidates = [
        article.standfirst,
        *(useful[1:] if article_title_is_desk else useful),
        article.body,
    ]
    detail = next(
        (
            item
            for item in detail_candidates
            if item.strip()
            and not (
                item.strip()
                .casefold()
                .rstrip(".")
                .startswith(headline.strip().casefold().rstrip("."))
                or headline.strip()
                .casefold()
                .rstrip(".")
                .startswith(item.strip().casefold().rstrip("."))
            )
            and not item.casefold().startswith(generic_starts)
        ),
        article.body,
    )
    return _limited_words(headline, 13), _limited_words(detail, 20)


def newspaper_from_verified_script(script: EpisodeScript) -> NewspaperIssue:
    """Emergency offline fallback for editions created before the independent writer."""

    urls = [
        url.rstrip(".,);]") for url in re.findall(r"https://[^\s]+", "\n".join(script.show_notes))
    ]
    articles: list[NewspaperArticle] = []
    generic_starts = (
        "we begin with",
        "we open today",
        "this edition",
        "this section",
        "in today's episode",
        "welcome to",
        "moving into",
        "moving to",
        "turning to",
    )
    for section in script.sections[:8]:
        sentences = [
            sentence
            for sentence in _complete_sentences(section.narration)
            if not (
                sentence.casefold().startswith(generic_starts)
                or sentence.casefold().startswith(f"in {section.name.value.casefold()}")
                or any(
                    re.search(
                        rf"\b{re.escape(host.casefold())}\b",
                        sentence.casefold(),
                    )
                    for host in script.hosts
                )
            )
        ]
        if not sentences:
            sentences = _complete_sentences(section.narration)
        standfirst = sentences[0] if sentences else section.name.value
        body_sentences = sentences[1:]
        body = _limited_words(" ".join(body_sentences), 70)
        if not body:
            body = "Further verified context is retained in the private source record."
        article = NewspaperArticle(
            title=section.name.value,
            body=body,
            source_urls=urls[:5],
            bullet_points=[],
            section_label=section.name.value,
            standfirst=standfirst,
            story_ids=list(section.story_ids),
        )
        articles.append(article)
    if not articles:
        articles = [
            NewspaperArticle(
                title="The Daily Nexus",
                body=_limited_words(script.conclusion_text, 180),
                source_urls=urls[:5],
                bullet_points=[],
                section_label="Briefing",
                standfirst="The verified developments that shaped the day.",
            )
        ]
    tih_articles = [
        article for article in articles if "today in history" in article.section_label.casefold()
    ]
    news_articles = [article for article in articles if article not in tih_articles]
    if not news_articles:
        news_articles = articles
    section_names = [article.title for article in news_articles[:4]]
    topics = ", ".join(section_names[:-1])
    if len(section_names) > 1:
        topics = f"{topics} and {section_names[-1]}"
    elif section_names:
        topics = section_names[0]
    visual_items = []
    for article in news_articles[:5]:
        headline, detail = _news_visual_copy(article)
        visual_items.append(
            NewspaperVisualItem(
                value=article.section_label,
                label=headline,
                detail=detail,
                story_ids=list(article.story_ids),
            )
        )
    return NewspaperIssue(
        headline=f"The decisive shifts across {topics}" if topics else script.title,
        deck="What changed, why it matters, and the developments worth watching next.",
        lead=" ".join(article.standfirst for article in news_articles[:2]),
        articles=articles,
        data_points=[],
        sources=script.show_notes[:12],
        kicker="The morning signal",
        pull_quote=(
            next(
                (
                    sentence
                    for sentence in _complete_sentences(script.conclusion_text)
                    if len(sentence.split()) >= 12
                    and "thank" not in sentence.casefold()
                    and "listen" not in sentence.casefold()
                    and "the daily nexus" not in sentence.casefold()
                    and "join us" not in sentence.casefold()
                    and "daily briefing" not in sentence.casefold()
                    and "stay informed" not in sentence.casefold()
                ),
                "",
            )
            or (
                "The strongest developments are the ones that change present choices, "
                "not merely the volume of the morning news."
            )
        ),
        briefs=[],
        executive_summary=[
            NewspaperVisualItem(
                value=value,
                label=_limited_words(
                    news_articles[min(index, len(news_articles) - 1)].title,
                    8,
                ),
                detail=(
                    news_articles[min(index, len(news_articles) - 1)].standfirst
                    or _complete_sentences(news_articles[min(index, len(news_articles) - 1)].body)[
                        0
                    ]
                ),
                story_ids=list(news_articles[min(index, len(news_articles) - 1)].story_ids),
            )
            for index, value in enumerate(("SHIFT", "IMPACT", "WATCH"))
        ],
        visuals=[
            NewspaperVisual(
                kind="news_grid",
                title="Five developments to know today",
                caption="The essential fact behind each leading story.",
                items=visual_items,
                source_urls=urls[:5],
            )
        ],
    )


def is_legacy_script_style_issue(issue: NewspaperIssue) -> bool:
    lead = issue.lead.casefold()
    pull_quote = issue.pull_quote.casefold()
    headline = issue.headline.casefold()
    deck = issue.deck.casefold()
    return (
        headline.startswith("the decisive shifts across")
        or deck.startswith("what changed, why it matters")
        or any(
            marker in lead
            for marker in (
                "i'm dario",
                "i am dario",
                "welcome to the daily nexus",
                "in today's episode",
                "this edition distills the verified reporting",
            )
        )
        or pull_quote.startswith("the useful story is")
    )


class NewspaperRenderer:
    PAGE_BACKGROUND = "#F4E3BD"
    PAPER = "#FFF4D6"
    INK = "#21130B"
    AMBER = "#D75A12"
    DARK_AMBER = "#6E2A0D"
    GOLD = "#E7B65F"
    MUTED = "#795B43"
    PALE = "#EED39A"

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _paragraph(text: str, style):
        from reportlab.platypus import Paragraph

        safe_text = (
            html.escape(_percent_symbols(text)).replace("\n\n", "<br/><br/>").replace("\n", "<br/>")
        )
        return Paragraph(safe_text, style)

    @staticmethod
    def _paragraph_with_emphasis(
        text: str,
        style,
        phrases: list[str] | None = None,
    ):
        from reportlab.platypus import Paragraph

        text = _percent_symbols(text)
        selected: list[str] = []
        for phrase in phrases or []:
            normalized = " ".join(phrase.split())
            if normalized and normalized.casefold() not in {item.casefold() for item in selected}:
                selected.append(normalized)
        if not selected:
            for match in re.finditer(
                r"(?:[$€£]\s*)?\d[\d,.]*(?:\s*(?:%|percent|million|billion|"
                r"trillion|gigawatt|years?|days?|months?))?",
                text,
                flags=re.IGNORECASE,
            ):
                value = match.group(0).strip()
                if value and value.casefold() not in {item.casefold() for item in selected}:
                    selected.append(value)
                if len(selected) == 4:
                    break
        if not selected:
            return NewspaperRenderer._paragraph(text, style)
        pattern = re.compile(
            "|".join(re.escape(value) for value in sorted(selected, key=len, reverse=True)),
            flags=re.IGNORECASE,
        )
        parts: list[str] = []
        cursor = 0
        for match in pattern.finditer(text):
            parts.append(html.escape(text[cursor : match.start()]))
            parts.append(f"<b>{html.escape(match.group(0))}</b>")
            cursor = match.end()
        parts.append(html.escape(text[cursor:]))
        markup = "".join(parts).replace("\n\n", "<br/><br/>").replace("\n", "<br/>")
        return Paragraph(markup, style)

    @staticmethod
    def _split_articles(
        articles: list[NewspaperArticle],
    ) -> tuple[list[NewspaperArticle], list[NewspaperArticle]]:
        if len(articles) <= 1:
            return articles, []
        for article in articles:
            label = f"{article.section_label} {article.title}".casefold()
            if "today in history" in label:
                return [article], [item for item in articles if item is not article]
        return articles[:1], articles[1:]

    @staticmethod
    def _topic_visual(issue: NewspaperIssue) -> NewspaperVisual:
        items = []
        for index, article in enumerate(issue.articles[:5], start=1):
            headline = article.title
            items.append(
                NewspaperVisualItem(
                    value=(article.section_label or f"Story {index}"),
                    label=headline,
                    detail="",
                    story_ids=list(article.story_ids),
                )
            )
        return NewspaperVisual(
            kind="news_grid",
            title="Inside this edition",
            caption="Read the complete developments in the articles below.",
            items=items,
            source_urls=[],
        )

    def _draw_masthead(
        self,
        drawing,
        episode_date: date,
        *,
        page_number: int,
        page_count: int,
        edition_name: str = "",
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase.pdfmetrics import stringWidth

        width, height = A4
        margin = 30
        bar_height = 80 if page_number == 1 else 62
        drawing.setFillColor(HexColor(self.PAGE_BACKGROUND))
        drawing.rect(0, 0, width, height, fill=1, stroke=0)
        drawing.setFillColor(HexColor(self.INK))
        drawing.rect(0, height - bar_height, width, bar_height, fill=1, stroke=0)
        drawing.setFillColor(HexColor(self.AMBER))
        drawing.rect(0, height - bar_height - 5, width, 5, fill=1, stroke=0)

        for offset in (9, 14, 19):
            drawing.setStrokeColor(HexColor(self.DARK_AMBER))
            drawing.setLineWidth(0.35)
            drawing.line(width - 92 + offset, height - bar_height, width - 44 + offset, height)

        logo_path = self.settings.project_dir / "assets" / "tdn-icon-transparent.png"
        logo_size = 49 if page_number == 1 else 35
        if logo_path.is_file():
            drawing.drawImage(
                str(logo_path),
                margin,
                height - bar_height + ((bar_height - logo_size) / 2),
                width=logo_size,
                height=logo_size,
                preserveAspectRatio=True,
                mask="auto",
            )
        drawing.setFillColor(HexColor(self.PAPER))
        drawing.setFont("Helvetica-Bold", 21 if page_number == 1 else 16)
        drawing.drawString(
            margin + logo_size + 11,
            height - (43 if page_number == 1 else 35),
            "THE DAILY NEXUS",
        )
        edition_label = " ".join(edition_name.split()) or "Morning Edition"
        edition_label = (
            f"{edition_label.upper()}  /  {episode_date.strftime('%A %d %B %Y').upper()}"
        )
        label_x = margin + logo_size + 12
        label_y = height - (59 if page_number == 1 else 49)
        label_limit = width - label_x - 194
        font_size = 6.8
        while (
            font_size > 4.8
            and stringWidth(
                edition_label,
                "Courier-Bold",
                font_size,
            )
            > label_limit
        ):
            font_size -= 0.3
        if stringWidth(edition_label, "Courier-Bold", font_size) > label_limit:
            # Preserve complete words when a deliberately long run name has to
            # share the masthead with its editorial credit.
            words = edition_label.split()
            kept: list[str] = []
            for word in words:
                candidate = " ".join([*kept, word, "…"])
                if kept and stringWidth(candidate, "Courier-Bold", font_size) > label_limit:
                    break
                kept.append(word)
            edition_label = " ".join(kept).rstrip(" /") + " …"
        drawing.setFont("Courier-Bold", font_size)
        drawing.drawString(
            label_x,
            label_y,
            edition_label,
        )
        drawing.setFont("Courier", 5.9)
        drawing.drawRightString(
            width - margin,
            height - (44 if page_number == 1 else 34),
            "EDITED + PRODUCED BY DARIO NOVELLI",
        )
        drawing.drawRightString(
            width - margin,
            height - (59 if page_number == 1 else 48),
            f"PRIVATE EDITION  /  {page_number} OF {page_count}",
        )

    def _draw_tab(
        self,
        drawing,
        text: str,
        x: float,
        y: float,
        *,
        dark: bool = False,
        max_width: float = 170,
    ) -> float:
        from reportlab.lib.colors import HexColor
        from reportlab.pdfbase.pdfmetrics import stringWidth

        label = _limited_words(text, 5).upper()
        width = min(max_width, stringWidth(label, "Courier-Bold", 6.6) + 16)
        drawing.setFillColor(HexColor(self.INK if dark else self.AMBER))
        drawing.roundRect(x, y - 13, width, 13, 2.5, fill=1, stroke=0)
        drawing.setFillColor(HexColor(self.PAPER))
        drawing.setFont("Courier-Bold", 6.6)
        drawing.drawString(x + 8, y - 9.5, label)
        return width

    def _draw_drop_cap_lead(
        self,
        drawing,
        text: str,
        *,
        x: float,
        top: float,
        width: float,
    ) -> float:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        clean = " ".join(text.split())
        if not clean:
            return top
        drawing.setFillColor(HexColor(self.AMBER))
        drawing.setFont("Times-Bold", 31)
        drawing.drawString(x, top - 28, clean[0])
        lead_style = ParagraphStyle(
            "DropLead",
            fontName="Times-Bold",
            fontSize=10.5,
            leading=13.5,
            textColor=self.INK,
            alignment=TA_LEFT,
        )
        lead = self._paragraph(clean[1:].lstrip(), lead_style)
        _, lead_height = lead.wrap(width - 31, 90)
        lead.drawOn(drawing, x + 31, top - lead_height)
        return top - max(34, lead_height) - 10

    def _article_blocks(
        self,
        articles: list[NewspaperArticle],
        *,
        width: float,
        body_size: float,
        feature: bool,
    ) -> list[dict]:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        title_size = body_size + (5.0 if feature else 3.2)
        title_style = ParagraphStyle(
            f"ArticleTitle-{body_size}-{feature}",
            fontName="Times-Bold",
            fontSize=title_size,
            leading=title_size + 1.3,
            textColor=self.INK,
            alignment=TA_LEFT,
            spaceAfter=3,
        )
        standfirst_style = ParagraphStyle(
            f"Standfirst-{body_size}-{feature}",
            fontName="Helvetica-Bold",
            fontSize=max(7.1, body_size - 0.4),
            leading=max(9.2, body_size + 1.5),
            textColor=self.DARK_AMBER,
            alignment=TA_LEFT,
        )
        body_style = ParagraphStyle(
            f"ArticleBody-{body_size}-{feature}",
            fontName="Times-Roman",
            fontSize=body_size,
            leading=body_size * 1.32,
            textColor=self.INK,
            alignment=TA_LEFT,
            firstLineIndent=10,
        )
        bullet_style = ParagraphStyle(
            f"ArticleBullets-{body_size}-{feature}",
            fontName="Helvetica",
            fontSize=max(6.9, body_size - 1.0),
            leading=max(8.8, body_size + 0.5),
            textColor=self.INK,
            leftIndent=8,
            firstLineIndent=-8,
            alignment=TA_LEFT,
        )
        blocks: list[dict] = []
        for article in articles:
            title = self._paragraph(article.title, title_style)
            standfirst = (
                self._paragraph_with_emphasis(
                    article.standfirst,
                    standfirst_style,
                    article.highlights,
                )
                if article.standfirst
                else None
            )
            body = self._paragraph_with_emphasis(
                article.body,
                body_style,
                article.highlights,
            )
            article_text = f"{article.standfirst} {article.body}"
            distinct_bullets = [
                point
                for point in article.bullet_points
                if _bullet_adds_distinct_information(point, article_text)
            ]
            bullet_text = "\n".join(f"\u2022 {point}" for point in distinct_bullets[:3])
            bullets = self._paragraph(bullet_text, bullet_style) if bullet_text else None
            _, title_height = title.wrap(width, 2_000)
            standfirst_height = 0.0
            if standfirst is not None:
                _, standfirst_height = standfirst.wrap(width, 2_000)
            _, body_height = body.wrap(width, 2_000)
            bullet_height = 0.0
            if bullets is not None:
                _, bullet_height = bullets.wrap(width, 2_000)
            blocks.append(
                {
                    "article": article,
                    "title": title,
                    "standfirst": standfirst,
                    "body": body,
                    "bullets": bullets,
                    "title_height": title_height,
                    "standfirst_height": standfirst_height,
                    "body_height": body_height,
                    "bullet_height": bullet_height,
                    "height": (
                        41
                        + title_height
                        + (standfirst_height + 5 if standfirst else 0)
                        + body_height
                        + (bullet_height + 7 if bullets else 0)
                    ),
                }
            )
        return blocks

    def _fit_single_column(
        self,
        articles: list[NewspaperArticle],
        *,
        width: float,
        available_height: float,
        feature: bool,
    ) -> list[dict]:
        for body_size in (9.3, 9.0, 8.7, 8.4, 8.1):
            blocks = self._article_blocks(
                articles,
                width=width,
                body_size=body_size,
                feature=feature,
            )
            if sum(block["height"] for block in blocks) <= available_height:
                return blocks
        raise NewspaperRenderError(
            "The newsletter stories cannot fit in the two-page layout; shorten the edition."
        )

    def _fit_two_columns(
        self,
        articles: list[NewspaperArticle],
        *,
        width: float,
        available_height: float,
    ) -> list[list[dict]]:
        if not articles:
            return [[], []]
        for body_size in (
            10.4,
            10.1,
            9.8,
            9.5,
            9.2,
            8.9,
            8.6,
            8.3,
            8.0,
            7.7,
        ):
            blocks = self._article_blocks(
                articles,
                width=width - 16,
                body_size=body_size,
                feature=False,
            )
            best = None
            # A simple consecutive split can reject a perfectly readable page
            # when two long articles happen to sit beside each other in the
            # editorial order. Evaluate every two-column assignment (at most
            # eight articles) while preserving the order within each column.
            for assignment in range(1, 1 << len(blocks)):
                if not assignment & 1:
                    continue
                columns = [
                    [block for index, block in enumerate(blocks) if assignment & (1 << index)],
                    [block for index, block in enumerate(blocks) if not assignment & (1 << index)],
                ]
                if len(blocks) > 1 and not columns[1]:
                    continue
                heights = [sum(block["height"] for block in column) for column in columns]
                if max(heights) > available_height:
                    continue
                score = (max(heights), abs(heights[0] - heights[1]))
                if best is None or score < best[0]:
                    best = (score, columns)
            if best is not None:
                return best[1]
        raise NewspaperRenderError(
            "The newsletter stories cannot fit in the two-page layout; shorten the edition."
        )

    def _fit_page_two_articles(
        self,
        articles: list[NewspaperArticle],
        *,
        visual: NewspaperVisual | None = None,
    ) -> list[list[dict]]:
        from reportlab.lib.pagesizes import A4

        width, height = A4
        margin = 30
        visual_top = height - 86
        if len(articles) <= 4:
            visual_height = 204
        elif len(articles) <= 5:
            visual_height = 194
        else:
            visual_height = 184
        if visual is not None:
            visual_height = max(
                visual_height, self._visual_plan(visual, width - 2 * margin)["height"],
            )
        stories_top = visual_top - visual_height - 13
        source_top = 60
        gap = 12
        column_width = (width - (2 * margin) - gap) / 2
        return self._fit_two_columns(
            articles,
            width=column_width,
            available_height=stories_top - source_top,
        )

    def _fit_page_three_articles(
        self,
        articles: list[NewspaperArticle],
    ) -> list[list[dict]]:
        from reportlab.lib.pagesizes import A4

        width, height = A4
        margin = 30
        gap = 12
        column_width = (width - (2 * margin) - gap) / 2
        stories_top = height - 105
        source_top = 60
        return self._fit_two_columns(
            articles,
            width=column_width,
            available_height=stories_top - source_top,
        )

    def _plan_article_pages(
        self,
        articles: list[NewspaperArticle],
        *,
        visual: NewspaperVisual | None = None,
    ) -> tuple[list[NewspaperArticle], list[NewspaperArticle]]:
        try:
            self._fit_page_two_articles(articles, visual=visual)
            return articles, []
        except NewspaperRenderError:
            pass

        # Preserve editorial order and put as much high-priority reporting as
        # possible on page two. Page three is used only when a readable
        # two-page layout cannot contain every complete article.
        for split_at in range(len(articles) - 1, 0, -1):
            page_two = articles[:split_at]
            page_three = articles[split_at:]
            try:
                self._fit_page_two_articles(page_two, visual=visual)
                self._fit_page_three_articles(page_three)
            except NewspaperRenderError:
                continue
            return page_two, page_three

        raise NewspaperRenderError(
            "The newsletter stories cannot fit legibly within the three-page maximum."
        )

    def _plan_first_page_articles(self, issue, first, remaining):
        """Use free feature space for complete reporting, not repeated teasers."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle

        width = A4[0] - 60
        headline = self._paragraph(_limited_words(issue.headline, 18), ParagraphStyle(
            "MeasureHeadline", fontName="Helvetica-Bold", fontSize=24, leading=25.2,
        ))
        deck = self._paragraph(_limited_words(issue.deck, 30), ParagraphStyle(
            "MeasureDeck", fontName="Helvetica", fontSize=10.3, leading=13,
        ))
        lead = self._paragraph(" ".join(issue.lead.split())[1:].lstrip(), ParagraphStyle(
            "MeasureLead", fontName="Times-Bold", fontSize=10.5, leading=13.5,
        ))
        available = (A4[1] - 106 - 22 - headline.wrap(width, 2000)[1] - 7
                     - deck.wrap(width, 2000)[1] - 9 - 12
                     - max(34, lead.wrap(337 - 31, 2000)[1]) - 10 - 39)
        briefs = self._reader_briefs(issue)
        reserve = 0
        if briefs:
            style = ParagraphStyle("MeasureBrief", fontName="Helvetica", fontSize=8,
                                   leading=8 * 1.28)
            reserve = max(144, 44 + sum(
                self._paragraph(f"{index:02d} / {text}", style).wrap(337 - 18, 2000)[1] + 5
                for index, text in enumerate(briefs, start=1)
            ))
        history_card = any(
            "today in history" in f"{article.section_label} {article.title}".casefold()
            for article in first
        )
        chosen, rest = list(first), list(remaining)
        while len(rest) > 1 and len(chosen) < 3:
            try:
                self._fit_single_column(
                    [*chosen, rest[0]], width=337 - (16 if history_card else 0),
                    available_height=available - reserve, feature=True,
                )
            except NewspaperRenderError:
                break
            chosen.append(rest.pop(0))
        return chosen, rest

    def _draw_article_blocks(
        self,
        drawing,
        blocks: list[dict],
        *,
        x: float,
        top: float,
        width: float,
        cards: bool,
    ) -> float:
        from reportlab.lib.colors import HexColor

        cursor = top
        for block in blocks:
            if cards:
                drawing.setFillColor(HexColor(self.PAPER))
                drawing.roundRect(
                    x,
                    cursor - block["height"] + 3,
                    width,
                    block["height"] - 3,
                    5,
                    fill=1,
                    stroke=0,
                )
                drawing.setFillColor(HexColor(self.AMBER))
                drawing.roundRect(
                    x,
                    cursor - block["height"] + 3,
                    3,
                    block["height"] - 3,
                    1.5,
                    fill=1,
                    stroke=0,
                )
                # A small rising sun echoes the Nexus mark without turning the
                # article corner into a radar/crosshair decoration.
                glyph_x = x + width - 15
                glyph_y = cursor - 10
                drawing.setFillColor(HexColor(self.AMBER))
                drawing.circle(glyph_x, glyph_y + 2.2, 4.2, fill=1, stroke=0)
                drawing.setStrokeColor(HexColor(self.GOLD))
                drawing.setLineWidth(0.65)
                for offset, half_width in ((0, 7), (-2.5, 5.2), (-5, 3.3)):
                    drawing.line(
                        glyph_x - half_width,
                        glyph_y + offset,
                        glyph_x + half_width,
                        glyph_y + offset,
                    )
                content_x = x + 9
            else:
                content_x = x
            content_width = width - (16 if cards else 0)
            tab_y = cursor - 1
            self._draw_tab(
                drawing,
                block["article"].section_label,
                content_x,
                tab_y,
                max_width=min(150, content_width),
            )
            cursor -= 20
            block["title"].drawOn(
                drawing,
                content_x,
                cursor - block["title_height"],
            )
            cursor -= block["title_height"] + 4
            if block["standfirst"] is not None:
                block["standfirst"].drawOn(
                    drawing,
                    content_x,
                    cursor - block["standfirst_height"],
                )
                cursor -= block["standfirst_height"] + 5
            block["body"].drawOn(
                drawing,
                content_x,
                cursor - block["body_height"],
            )
            cursor -= block["body_height"] + 5
            if block["bullets"] is not None:
                drawing.setStrokeColor(HexColor(self.GOLD))
                drawing.setLineWidth(1)
                drawing.line(content_x, cursor + 2, content_x + content_width, cursor + 2)
                cursor -= block["bullet_height"]
                block["bullets"].drawOn(
                    drawing,
                    content_x,
                    cursor,
                )
                cursor -= 7
            if not cards:
                drawing.setStrokeColor(HexColor(self.PALE))
                drawing.setLineWidth(0.65)
                drawing.line(content_x, cursor, content_x + content_width, cursor)
            cursor -= 12
        return cursor

    def _visual_plan(self, visual: NewspaperVisual, width: float, *, dark: bool = True) -> dict:
        """Measure complete visual copy before allocating page space."""
        from reportlab.lib.colors import HexColor
        from reportlab.lib.styles import ParagraphStyle

        if not 1 <= len(visual.items) <= 6:
            raise NewspaperRenderError("A visual must contain one to six complete items")
        content_width = width - 20
        columns = 1 if visual.kind in {"bar_chart", "timeline", "process"} else 2
        cell_width = (content_width - 8 * (columns - 1)) / columns
        foreground = HexColor(self.PAPER if dark else self.INK)
        secondary = HexColor(self.GOLD if dark else self.DARK_AMBER)
        styles = [
            ParagraphStyle("VisualValue", fontName="Courier-Bold", fontSize=9.0, leading=11,
                           textColor=secondary),
            ParagraphStyle("VisualLabel", fontName="Helvetica-Bold", fontSize=8.0, leading=10,
                           textColor=foreground),
            ParagraphStyle("VisualDetail", fontName="Helvetica", fontSize=7.4, leading=9.4,
                           textColor=foreground),
        ]
        title = self._paragraph(
            visual.title, ParagraphStyle("VisualTitle", fontName="Helvetica-Bold",
                                        fontSize=12.5, leading=14.5, textColor=foreground),
        )
        caption = self._paragraph(
            visual.caption, ParagraphStyle("VisualCaption", fontName="Helvetica",
                                          fontSize=7.5, leading=9.5, textColor=secondary),
        )
        title_height = title.wrap(content_width, 2000)[1]
        caption_height = caption.wrap(content_width, 2000)[1]
        header_height = 49 + title_height + caption_height
        rows = []
        for start in range(0, len(visual.items), columns):
            cells = []
            for item in visual.items[start:start + columns]:
                text_width = cell_width - 16
                if visual.kind == "bar_chart":
                    if item.magnitude is None or not math.isfinite(item.magnitude):
                        raise NewspaperRenderError("Chart magnitudes must be finite numbers")
                    text_width = min(160, content_width * 0.36) - 8
                parts = [
                    self._paragraph(text, style)
                    for text, style in zip(
                        (item.value, item.label, item.detail), styles, strict=True,
                    )
                    if text.strip()
                ]
                heights = [part.wrap(text_width, 2000)[1] for part in parts]
                if visual.kind == "bar_chart":
                    # Value at right, label/detail at left, signed bar in between.
                    heights[0] = parts[0].wrap(min(82, content_width * 0.18), 2000)[1]
                    required = max(sum(heights[1:]) + 12, heights[0] + 17)
                else:
                    required = sum(heights) + 4 * (len(parts) - 1) + 16
                cells.append(dict(item=item, parts=parts, heights=heights, required=required))
            rows.append(dict(cells=cells, height=max(cell["required"] for cell in cells)))
        return dict(title=title, caption=caption, title_height=title_height,
                    caption_height=caption_height, header_height=header_height,
                    columns=columns, cell_width=cell_width, rows=rows,
                    height=header_height + sum(row["height"] for row in rows)
                    + 7 * max(0, len(rows) - 1) + 10)

    def _draw_visual(
        self, drawing, visual: NewspaperVisual, *,
        x: float, top: float, width: float, height: float, dark: bool,
    ) -> None:
        from reportlab.lib.colors import HexColor

        plan = self._visual_plan(visual, width, dark=dark)
        if plan["height"] > height + 0.1:
            raise NewspaperRenderError("Complete visual copy exceeds its measured panel")
        secondary = self.GOLD if dark else self.DARK_AMBER
        drawing.setFillColor(HexColor(self.INK if dark else self.PAPER))
        drawing.roundRect(x, top - height, width, height, 7, fill=1, stroke=0)
        drawing.setStrokeColor(HexColor(self.AMBER))
        drawing.setLineWidth(1.2)
        drawing.line(x + 10, top - 28, x + width - 10, top - 28)
        drawing.setFillColor(HexColor(secondary))
        drawing.setFont("Courier-Bold", 6.2)
        kind = "news grid" if visual.kind in {"signal_map", "decision_matrix"} else (
            visual.kind.replace("_", " ")
        )
        drawing.drawString(x + 10, top - 17, f"VISUAL BRIEF  /  {kind.upper()}")
        cursor = top - 37
        plan["title"].drawOn(drawing, x + 10, cursor - plan["title_height"])
        cursor -= plan["title_height"] + 4
        plan["caption"].drawOn(drawing, x + 10, cursor - plan["caption_height"])
        cursor = top - plan["header_height"]
        magnitudes = [item.magnitude for item in visual.items if item.magnitude is not None]
        maximum = max((abs(value) for value in magnitudes), default=0) or 1
        signed = any(value < 0 for value in magnitudes)
        for row in plan["rows"]:
            for column, cell in enumerate(row["cells"]):
                cell_x = x + 10 + column * (plan["cell_width"] + 8)
                item = cell["item"]
                if visual.kind == "bar_chart":
                    label_width = min(160, (width - 20) * 0.36)
                    value_width = min(82, (width - 20) * 0.18)
                    text_y = cursor - 5
                    for part, part_height in zip(
                        cell["parts"][1:], cell["heights"][1:], strict=True,
                    ):
                        part.drawOn(drawing, cell_x, text_y - part_height)
                        text_y -= part_height
                    value = cell["parts"][0]
                    value.drawOn(drawing, x + width - 10 - value_width,
                                 cursor - 5 - cell["heights"][0])
                    bar_x = cell_x + label_width + 8
                    bar_width = width - 20 - label_width - value_width - 24
                    bar_y = cursor - row["height"] / 2 - 4
                    drawing.setFillColor(HexColor(self.DARK_AMBER if dark else self.PALE))
                    drawing.rect(bar_x, bar_y, bar_width, 8, fill=1, stroke=0)
                    origin = bar_x + (bar_width / 2 if signed else 0)
                    extent = (bar_width / (2 if signed else 1)) * abs(item.magnitude) / maximum
                    if extent:
                        drawing.setFillColor(HexColor(self.AMBER))
                        drawing.rect(origin - extent if item.magnitude < 0 else origin,
                                     bar_y, extent, 8, fill=1, stroke=0)
                    if signed:
                        drawing.setStrokeColor(HexColor(secondary))
                        drawing.setLineWidth(0.7)
                        drawing.line(origin, bar_y - 2, origin, bar_y + 10)
                else:
                    drawing.setFillColor(HexColor(self.DARK_AMBER if dark else self.PALE))
                    drawing.roundRect(cell_x, cursor - row["height"], plan["cell_width"],
                                      row["height"], 4, fill=1, stroke=0)
                    text_y = cursor - 8
                    for part, part_height in zip(cell["parts"], cell["heights"], strict=True):
                        part.drawOn(drawing, cell_x + 8, text_y - part_height)
                        text_y -= part_height + 4
                    if visual.kind in {"timeline", "process"}:
                        drawing.setFillColor(HexColor(self.AMBER))
                        drawing.circle(cell_x - 3, cursor - 12, 2.5, fill=1, stroke=0)
            cursor -= row["height"] + 7

    @staticmethod
    def _executive_items(issue: NewspaperIssue) -> list[NewspaperVisualItem]:
        tih_story_ids = {
            story_id
            for article in issue.articles
            if "today in history" in f"{article.section_label} {article.title}".casefold()
            for story_id in article.story_ids
        }
        news_articles = [
            article for article in issue.articles if not set(article.story_ids) & tih_story_ids
        ] or issue.articles
        eligible_summary = [
            item for item in issue.executive_summary if not set(item.story_ids) & tih_story_ids
        ]
        if eligible_summary:
            repaired: list[NewspaperVisualItem] = []
            weak_markers = (
                "retained from",
                "supplied evidence",
                "open question when",
            )
            for index, item in enumerate(eligible_summary[:3]):
                detail = item.detail
                if not detail or any(marker in detail.casefold() for marker in weak_markers):
                    article = news_articles[min(index, len(news_articles) - 1)]
                    sentences = _complete_sentences(article.standfirst or article.body)
                    detail = sentences[0] if sentences else article.title
                repaired.append(
                    NewspaperVisualItem(
                        value=item.value,
                        label=item.label,
                        detail=detail,
                        magnitude=item.magnitude,
                        story_ids=list(item.story_ids),
                    )
                )
            while len(repaired) < 3:
                index = len(repaired)
                article = news_articles[min(index, len(news_articles) - 1)]
                repaired.append(
                    NewspaperVisualItem(
                        value=("SHIFT", "IMPACT", "WATCH")[index],
                        label=article.title,
                        detail=article.standfirst or article.body,
                        story_ids=list(article.story_ids),
                    )
                )
            return repaired
        fallback = [
            *issue.data_points,
            *(brief.text for brief in issue.briefs),
            *(article.standfirst or article.body for article in issue.articles),
        ]
        labels = ("SHIFT", "IMPACT", "WATCH")
        return [
            NewspaperVisualItem(
                value=label,
                label=_limited_words(
                    fallback[index] if index < len(fallback) else issue.deck,
                    10,
                ),
                detail=(
                    _limited_words(
                        news_articles[min(index, len(news_articles) - 1)].body,
                        24,
                    )
                ),
            )
            for index, label in enumerate(labels)
        ]

    def _draw_executive_lens(
        self,
        drawing,
        issue: NewspaperIssue,
        *,
        x: float,
        top: float,
        width: float,
        height: float,
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        drawing.setFillColor(HexColor(self.INK))
        drawing.roundRect(x, top - height, width, height, 7, fill=1, stroke=0)
        drawing.setStrokeColor(HexColor(self.AMBER))
        drawing.setLineWidth(0.8)
        drawing.line(x + 10, top - 29, x + width - 10, top - 29)
        drawing.setFillColor(HexColor(self.GOLD))
        drawing.setFont("Courier-Bold", 6.2)
        drawing.drawString(x + 10, top - 17, "EXECUTIVE SIGNAL  /  03")

        # Use the rising-sun mark from the Nexus language, rather than the
        # radar/crosshair that made this consumer-facing panel feel like a
        # technical dashboard.
        sun_x = x + width - 28
        sun_y = top - 18
        drawing.setFillColor(HexColor(self.AMBER))
        drawing.circle(sun_x, sun_y + 2.5, 5.0, fill=1, stroke=0)
        drawing.setStrokeColor(HexColor(self.GOLD))
        drawing.setLineWidth(0.7)
        for offset, half_width in ((0, 10), (-3.2, 7.4), (-6.4, 4.6)):
            drawing.line(
                sun_x - half_width,
                sun_y + offset,
                sun_x + half_width,
                sun_y + offset,
            )

        label_style = ParagraphStyle(
            "ExecutiveLensLabel",
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=10,
            textColor=self.PAPER,
            alignment=TA_LEFT,
        )
        detail_style = ParagraphStyle(
            "ExecutiveLensDetail",
            fontName="Helvetica",
            fontSize=7.4,
            leading=9.4,
            textColor=self.GOLD,
            alignment=TA_LEFT,
        )
        items = self._executive_items(issue)
        content_top = top - 43
        row_height = (height - 50) / max(1, len(items))
        for index, item in enumerate(items):
            row_top = content_top - index * row_height
            drawing.setFillColor(HexColor(self.AMBER))
            drawing.roundRect(
                x + 10,
                row_top - 13,
                42,
                13,
                2.5,
                fill=1,
                stroke=0,
            )
            drawing.setFillColor(HexColor(self.PAPER))
            drawing.setFont("Courier-Bold", 5.6)
            drawing.drawString(
                x + 16,
                row_top - 9.5,
                _short_visual_text(item.value, words=2, characters=11).upper(),
            )
            label = self._paragraph(item.label, label_style)
            _, label_height = label.wrap(width - 65, 28)
            label.drawOn(drawing, x + 59, row_top - label_height)
            detail = self._paragraph(item.detail, detail_style)
            _, detail_height = detail.wrap(width - 20, 25)
            if max(15, label_height) + 5 + detail_height > row_height - 8 + 0.1:
                raise NewspaperRenderError("Complete executive signal exceeds its panel")
            detail.drawOn(
                drawing,
                x + 10,
                row_top - max(15, label_height) - 5 - detail_height,
            )
            if index < len(items) - 1:
                drawing.setStrokeColor(HexColor(self.DARK_AMBER))
                drawing.setLineWidth(0.45)
                drawing.line(
                    x + 10,
                    row_top - row_height + 4,
                    x + width - 10,
                    row_top - row_height + 4,
                )

    @staticmethod
    def _reader_briefs(issue: NewspaperIssue) -> list[str]:
        tih_ids = {
            identifier for article in issue.articles
            if "today in history" in f"{article.section_label} {article.title}".casefold()
            for identifier in article.story_ids
        }
        existing = {
            _reader_copy_key(sentence)
            for article in issue.articles
            for sentence in _complete_sentences(f"{article.standfirst} {article.body}")
        }
        seen = set(existing)
        result = []
        texts = ([brief.text for brief in issue.briefs if not set(brief.story_ids) & tih_ids]
                 if issue.briefs else issue.data_points)
        for text in texts:
            key = _reader_copy_key(text)
            if key and key not in seen:
                result.append(text)
                seen.add(key)
        return result

    def _draw_briefs(
        self, drawing, issue: NewspaperIssue, *,
        x: float, top: float, width: float, height: float,
    ) -> float:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.styles import ParagraphStyle

        briefs = self._reader_briefs(issue)
        if not briefs:
            return 0
        blocks = None
        for size in (8.0, 7.6, 7.2, 6.8):
            style = ParagraphStyle("Briefs", fontName="Helvetica", fontSize=size,
                                   leading=size * 1.28, textColor=self.INK)
            candidate = [self._paragraph(f"{index:02d} / {text}", style)
                         for index, text in enumerate(briefs, start=1)]
            heights = [paragraph.wrap(width - 18, 2000)[1] for paragraph in candidate]
            if sum(heights) + 5 * len(heights) <= height - 38:
                blocks = list(zip(candidate, heights, strict=True))
                break
        if blocks is None:
            raise NewspaperRenderError("Complete secondary briefs exceed their panel")
        height = min(height, 38 + sum(block[1] + 5 for block in blocks))
        drawing.setFillColor(HexColor(self.PAPER))
        drawing.roundRect(x, top - height, width, height, 6, fill=1, stroke=0)
        self._draw_tab(drawing, "Secondary developments", x + 9, top - 9,
                       max_width=width - 18)
        cursor = top - 30
        for paragraph, paragraph_height in blocks:
            paragraph.drawOn(drawing, x + 9, cursor - paragraph_height)
            cursor -= paragraph_height + 5
        return height

    def _draw_pull_quote(
        self,
        drawing,
        issue: NewspaperIssue,
        *,
        x: float,
        top: float,
        width: float,
        height: float,
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        quote_text = (
            issue.pull_quote or (issue.data_points[0] if issue.data_points else "") or issue.deck
        )
        drawing.setFillColor(HexColor(self.AMBER))
        drawing.roundRect(x, top - height, width, height, 6, fill=1, stroke=0)
        drawing.setFillColor(HexColor(self.PAPER))
        drawing.setFont("Times-Bold", 28)
        drawing.drawString(x + 9, top - 29, "\u201c")
        quote_style = ParagraphStyle(
            "PullQuote",
            fontName="Times-BoldItalic",
            fontSize=9.2,
            leading=11.2,
            textColor=self.PAPER,
            alignment=TA_LEFT,
        )
        quote = self._paragraph(quote_text, quote_style)
        _, quote_height = quote.wrap(width - 24, height - 30)
        if quote_height > height - 55:
            raise NewspaperRenderError("Complete editorial takeaway exceeds its panel")
        quote.drawOn(drawing, x + 13, top - 33 - quote_height)
        drawing.setFont("Courier-Bold", 5.8)
        drawing.drawString(x + 13, top - height + 10, "EDITORIAL TAKEAWAY")

    def _draw_teasers(
        self,
        drawing,
        articles: list[NewspaperArticle],
        *,
        x: float,
        top: float,
        width: float,
        height: float,
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        if not articles:
            return
        drawing.setStrokeColor(HexColor(self.AMBER))
        drawing.setLineWidth(0.8)
        drawing.line(x, top, x + width, top)
        self._draw_tab(drawing, "Also inside", x, top - 9, max_width=120)
        gap = 8
        card_width = (width - gap) / 2
        card_count = min(4 if height >= 260 else 2, len(articles))
        rows = 2 if card_count > 2 else 1
        available_card_height = height - 31
        card_height = (available_card_height - (gap * (rows - 1))) / rows
        title_style = ParagraphStyle(
            "TeaserTitle",
            fontName="Times-Bold",
            fontSize=10.4,
            leading=11.2,
            textColor=self.INK,
            alignment=TA_LEFT,
        )
        for index, article in enumerate(articles[:card_count]):
            title = self._paragraph(article.title, title_style)
            _, title_height = title.wrap(card_width - 16, 2_000)
            if title_height > card_height - 40:
                # This is optional navigation; the complete article is printed
                # on a later page. Never crop its heading to fill a small card.
                continue
            column = index % 2
            row = index // 2
            card_x = x + column * (card_width + gap)
            card_top = top - 31 - row * (card_height + gap)
            drawing.setFillColor(HexColor(self.PAPER))
            drawing.roundRect(
                card_x,
                card_top - card_height,
                card_width,
                card_height,
                5,
                fill=1,
                stroke=0,
            )
            drawing.setFillColor(HexColor(self.DARK_AMBER))
            drawing.setFont("Courier-Bold", 5.5)
            drawing.drawString(
                card_x + 8,
                card_top - 13,
                _short_visual_text(article.section_label, words=3, characters=22).upper(),
            )
            title.drawOn(drawing, card_x + 8, card_top - 22 - title_height)
            # Keep the small-card marker consistent with the rising sun used
            # throughout the edition instead of leaving an unexplained dot.
            glyph_x = card_x + card_width - 12
            glyph_y = card_top - card_height + 10
            drawing.setFillColor(HexColor(self.AMBER))
            drawing.circle(glyph_x, glyph_y + 1.8, 3, fill=1, stroke=0)
            drawing.setStrokeColor(HexColor(self.GOLD))
            drawing.setLineWidth(0.5)
            for offset, half_width in ((0, 5.2), (-1.8, 3.9), (-3.6, 2.4)):
                drawing.line(
                    glyph_x - half_width,
                    glyph_y + offset,
                    glyph_x + half_width,
                    glyph_y + offset,
                )

    def _draw_page_one(
        self,
        drawing,
        issue: NewspaperIssue,
        episode_date: date,
        articles: list[NewspaperArticle],
        *,
        page_count: int,
        edition_name: str = "",
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle

        width, height = A4
        margin = 30
        self._draw_masthead(
            drawing,
            episode_date,
            page_number=1,
            page_count=page_count,
            edition_name=edition_name,
        )
        y = height - 106
        self._draw_tab(drawing, issue.kicker, margin, y)
        y -= 22

        headline_style = ParagraphStyle(
            "Headline",
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=25.2,
            textColor=self.INK,
            alignment=TA_LEFT,
        )
        deck_style = ParagraphStyle(
            "Deck",
            fontName="Helvetica",
            fontSize=10.3,
            leading=13,
            textColor=self.DARK_AMBER,
            alignment=TA_LEFT,
        )
        headline = self._paragraph(_limited_words(issue.headline, 18), headline_style)
        _, headline_height = headline.wrap(width - (2 * margin), 74)
        headline.drawOn(drawing, margin, y - headline_height)
        y -= headline_height + 7
        deck = self._paragraph(_limited_words(issue.deck, 30), deck_style)
        _, deck_height = deck.wrap(width - (2 * margin), 44)
        deck.drawOn(drawing, margin, y - deck_height)
        y -= deck_height + 9
        drawing.setStrokeColor(HexColor(self.AMBER))
        drawing.setLineWidth(1.25)
        drawing.line(margin, y, width - margin, y)
        y -= 12

        left_width = 337
        gap = 18
        right_x = margin + left_width + gap
        right_width = width - margin - right_x
        left_top = self._draw_drop_cap_lead(
            drawing,
            re.sub(
                r"\bsingle-page edition\b",
                "two-page edition",
                issue.lead,
                flags=re.IGNORECASE,
            ),
            x=margin,
            top=y,
            width=left_width,
        )
        bottom = 39
        history_card = bool(
            articles and "today in history"
            in f"{articles[0].section_label} {articles[0].title}".casefold()
        )
        blocks = self._fit_single_column(
            articles,
            width=left_width - (16 if history_card else 0),
            available_height=left_top - bottom,
            feature=True,
        )
        article_bottom = self._draw_article_blocks(
            drawing,
            blocks,
            x=margin,
            top=left_top,
            width=left_width,
            cards=history_card,
        )
        teaser_space = article_bottom - bottom
        left_briefs = bool(self._reader_briefs(issue)) and teaser_space >= 140
        if left_briefs:
            self._draw_briefs(
                drawing, issue, x=margin, top=article_bottom + 2,
                width=left_width, height=teaser_space - 4,
            )
        elif teaser_space >= 125:
            self._draw_teasers(
                drawing,
                issue.articles[len(articles) : len(articles) + 4],
                x=margin,
                top=article_bottom + 2,
                width=left_width,
                height=teaser_space - 4,
            )

        sidebar_height = y - bottom
        executive_rows = []
        for item in self._executive_items(issue):
            label = self._paragraph(item.label, ParagraphStyle(
                "ExecutiveMeasureLabel", fontName="Helvetica-Bold", fontSize=8.2, leading=10,
            ))
            detail = self._paragraph(item.detail, ParagraphStyle(
                "ExecutiveMeasureDetail", fontName="Helvetica", fontSize=7.4, leading=9.4,
            ))
            executive_rows.append(max(15, label.wrap(right_width - 65, 2000)[1])
                                  + 5 + detail.wrap(right_width - 20, 2000)[1] + 8)
        executive_height = 50 + len(executive_rows) * max(executive_rows, default=0)
        quote_height = min(128, sidebar_height * 0.23)
        if executive_height + quote_height + 14 > sidebar_height:
            raise NewspaperRenderError("Complete sidebar copy exceeds the first page")
        briefs_height = sidebar_height - executive_height - quote_height - 14
        self._draw_executive_lens(
            drawing,
            issue,
            x=right_x,
            top=y,
            width=right_width,
            height=executive_height,
        )
        briefs_top = y - executive_height - 7
        right_briefs_height = 0
        if not left_briefs:
            right_briefs_height = self._draw_briefs(
                drawing, issue, x=right_x, top=briefs_top,
                width=right_width, height=briefs_height,
            )
        self._draw_pull_quote(
            drawing,
            issue,
            x=right_x,
            top=briefs_top - right_briefs_height - 7,
            width=right_width,
            height=quote_height,
        )

        drawing.setFillColor(HexColor(self.MUTED))
        drawing.setFont("Courier", 5.8)
        drawing.drawString(margin, 18, "TURN THE PAGE  /  VISUAL BRIEFING + MORE STORIES")
        drawing.drawRightString(width - margin, 18, "THE DAILY NEXUS")

    def _draw_page_two(
        self,
        drawing,
        issue: NewspaperIssue,
        episode_date: date,
        articles: list[NewspaperArticle],
        *,
        page_count: int,
        edition_name: str = "",
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle

        width, height = A4
        margin = 30
        self._draw_masthead(
            drawing,
            episode_date,
            page_number=2,
            page_count=page_count,
            edition_name=edition_name,
        )
        visual = issue.visuals[0] if issue.visuals else self._topic_visual(issue)
        visual_top = height - 86
        if len(articles) <= 4:
            visual_height = 204
        elif len(articles) <= 5:
            visual_height = 194
        else:
            visual_height = 184
        visual_height = max(visual_height, self._visual_plan(visual, width - 2 * margin)["height"])
        self._draw_visual(
            drawing,
            visual,
            x=margin,
            top=visual_top,
            width=width - (2 * margin),
            height=visual_height,
            dark=True,
        )

        stories_top = visual_top - visual_height - 13
        gap = 12
        column_width = (width - (2 * margin) - gap) / 2
        columns = self._fit_page_two_articles(articles, visual=visual)
        for index, blocks in enumerate(columns):
            self._draw_article_blocks(
                drawing,
                blocks,
                x=margin + index * (column_width + gap),
                top=stories_top,
                width=column_width,
                cards=True,
            )

        if page_count > 2:
            source_text = "CONTINUED  /  ADDITIONAL VERIFIED DEVELOPMENTS ON PAGE 3"
        else:
            domains = _source_domains(issue)
            source_text = (
                "SOURCE SITES  /  " + "  |  ".join(domains)
                if domains
                else "SOURCE SITES  /  Retained in the private edition record."
            )
        source_style = ParagraphStyle(
            "Sources",
            fontName="Courier-Bold" if page_count > 2 else "Courier",
            fontSize=5.8 if page_count > 2 else 5.0,
            leading=7.0 if page_count > 2 else 6.2,
            textColor=self.DARK_AMBER if page_count > 2 else self.MUTED,
            alignment=TA_LEFT,
        )
        sources = self._paragraph(source_text, source_style)
        _, source_height = sources.wrap(width - (2 * margin), 28)
        drawing.setStrokeColor(HexColor(self.DARK_AMBER))
        drawing.setLineWidth(0.55)
        drawing.line(margin, 43, width - margin, 43)
        sources.drawOn(drawing, margin, max(13, 39 - source_height))

    def _draw_page_three(
        self,
        drawing,
        issue: NewspaperIssue,
        episode_date: date,
        articles: list[NewspaperArticle],
        *,
        page_count: int,
        edition_name: str = "",
    ) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle

        width, height = A4
        margin = 30
        self._draw_masthead(
            drawing,
            episode_date,
            page_number=3,
            page_count=page_count,
            edition_name=edition_name,
        )
        self._draw_tab(
            drawing,
            "Continued // deeper read",
            margin,
            height - 76,
            max_width=170,
        )
        stories_top = height - 105
        gap = 12
        column_width = (width - (2 * margin) - gap) / 2
        columns = self._fit_page_three_articles(articles)
        for index, blocks in enumerate(columns):
            self._draw_article_blocks(
                drawing,
                blocks,
                x=margin + index * (column_width + gap),
                top=stories_top,
                width=column_width,
                cards=True,
            )

        domains = _source_domains(issue)
        source_text = (
            "SOURCE SITES  /  " + "  |  ".join(domains)
            if domains
            else "SOURCE SITES  /  Retained in the private edition record."
        )
        source_style = ParagraphStyle(
            "SourcesPageThree",
            fontName="Courier",
            fontSize=5.0,
            leading=6.2,
            textColor=self.MUTED,
            alignment=TA_LEFT,
        )
        sources = self._paragraph(source_text, source_style)
        _, source_height = sources.wrap(width - (2 * margin), 28)
        drawing.setStrokeColor(HexColor(self.DARK_AMBER))
        drawing.setLineWidth(0.55)
        drawing.line(margin, 43, width - margin, 43)
        sources.drawOn(drawing, margin, max(13, 39 - source_height))

    @staticmethod
    def _preview_paths(first_preview: Path, page_count: int) -> tuple[Path, ...]:
        match = re.match(r"^(.*)-1$", first_preview.stem)
        base = match.group(1) if match else first_preview.stem
        return tuple(
            (
                first_preview
                if page_number == 1
                else first_preview.with_name(f"{base}-{page_number}{first_preview.suffix}")
            )
            for page_number in range(1, page_count + 1)
        )

    def render(
        self,
        issue: NewspaperIssue,
        episode_date: date,
        pdf_path: Path,
        preview_path: Path,
        *,
        edition_name: str = "",
    ) -> NewspaperResult:
        try:
            import pymupdf as fitz
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except ImportError as exc:
            raise NewspaperRenderError(
                "ReportLab and PyMuPDF are required for the two-page newspaper."
            ) from exc

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        page_one_articles, page_two_articles = self._split_articles(issue.articles)
        page_one_articles, page_two_articles = self._plan_first_page_articles(
            issue, page_one_articles, page_two_articles,
        )
        visual = issue.visuals[0] if issue.visuals else self._topic_visual(issue)
        page_two_articles, page_three_articles = self._plan_article_pages(
            page_two_articles, visual=visual,
        )
        page_count = 3 if page_three_articles else 2

        drawing = canvas.Canvas(str(pdf_path), pagesize=A4)
        drawing.setTitle(
            " - ".join(
                part
                for part in (
                    "The Daily Nexus",
                    " ".join(edition_name.split()),
                    episode_date.isoformat(),
                )
                if part
            )
        )
        drawing.setAuthor("Dario Novelli")

        self._draw_page_one(
            drawing,
            issue,
            episode_date,
            page_one_articles,
            page_count=page_count,
            edition_name=edition_name,
        )
        drawing.showPage()
        self._draw_page_two(
            drawing,
            issue,
            episode_date,
            page_two_articles,
            page_count=page_count,
            edition_name=edition_name,
        )
        drawing.showPage()
        if page_three_articles:
            self._draw_page_three(
                drawing,
                issue,
                episode_date,
                page_three_articles,
                page_count=page_count,
                edition_name=edition_name,
            )
            drawing.showPage()
        drawing.save()

        preview_paths = self._preview_paths(preview_path, page_count)
        try:
            document = fitz.open(pdf_path)
            if document.page_count != page_count:
                raise NewspaperRenderError(f"newspaper PDF must contain exactly {page_count} pages")
            for page_number, output_path in enumerate(preview_paths):
                page = document.load_page(page_number)
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(1.8, 1.8),
                    alpha=False,
                )
                pixmap.save(output_path)
            document.close()
        except (OSError, RuntimeError, ValueError) as exc:
            raise NewspaperRenderError(f"Could not render newspaper previews: {exc}") from exc

        return NewspaperResult(
            pdf_path=pdf_path,
            preview_paths=preview_paths,
        )
