# Reviewing an edition

Review ordinary completed runs before changing editorial rules. Do not generate
extra episodes or restart failed ones merely to gather a quality sample. Keep
scripts, source passages, account settings and media links out of public issues,
fixtures and documentation.

## Coverage is not a duration quota

### Why a newsletter has no retained news

New extractions optionally include fixed editorial explanations in the existing
model response. They add no call, retry, duration quota or publication gate. The
application first measures retained news from validated and consolidated source
lineage; model explanations cannot override this coverage or approve a fact.

- No prepared body **and** article passages: `newsletter_no_supplied_text`, an
  observed input condition, not proof that the original email was empty.
- `newsletter_model_no_substantive_news`, `newsletter_model_promotional`,
  `newsletter_model_outside_sections` and `newsletter_model_insufficient_evidence`:
  model-reported, unverified explanations. Outside-sections explanations are used
  only when sections were configured. No free-text explanations are saved.
- `newsletter_reason_unknown`: absent, malformed, conflicting or unsupported
  explanations. Legacy records without measurements stay unmeasured, not zero.

These six counters partition newsletters without retained news. Input-cap flags
count newsletters whose original body or article text exceeded the existing
50,000-character preparation limit. They are not explanations for omission and
do not change that limit. Fetch failures and language never establish a cause.

Counts reach private progress even when extraction returns no useful news.
Completed local manifests also retain a per-input ledger with transient source
aliases, passage counts and fixed categories only. Hosted profiles and episode
metadata expose only bounded aggregate numbers to the authorized owner; no extra
mailbox identifiers, subjects, senders, passages or arbitrary model text are copied.
Cloud working files are ephemeral; do not promise hosted per-email diagnostics or
retroactive explanations for older episodes. Original-source factual checks,
audio selection, one-hour protection and no-spend safeguards remain unchanged.

### Source coverage and audio selection

- `sourceMix.newsletter_messages` counts collected messages, not stories or
  independently verified facts.
- `episodeBudget.represented_newsletters` counts newsletter sources represented
  by the audio shortlist. A received message need not contain an extractable news
  item; a gap does not by itself prove that Gmail failed or explain why it occurred.
- `sourceMix.extracted_story_records` counts candidates before consolidation.
  `duplicate_story_records` counts merged records, not every possible paraphrase.
- `episodeBudget.available_stories` and `selected_news_stories` refer to news
  candidates and the audio shortlist; TIH research does not inflate this news count.
  `sourceMix.omitted_news_stories` is outside-shortlist news, not silently missing
  required script coverage. Merely citing an ID still does not prove useful coverage.
- `source_passages_cited` describes retained evidence lineage, not a truth score
  or a count of sentences independently checked by a human.
- `newsletters_with_extracted_news` counts distinct collected newsletter sources
  supporting retained non-TIH news after consolidation. Its complement,
  `newsletters_without_extracted_news`, has no retained news candidate; that is
  not a claim of an empty email, blocked URL, irrelevance or factual rejection.
- `newsletters_in_audio` counts distinct newsletter sources behind selected news.
  `newsletters_outside_audio` counts sources with retained news but none in the
  audio shortlist, not the number of stories excluded from it. A source counts
  once even when several stories cite it. TIH, research and unknown IDs never
  inflate these four counts.

Extraction counts are available before a no-content failure; audio-selection
counts appear only after planning. The monitor distinguishes unmeasured audio
selection from measured zero. New editions retain these bounded numbers in
owner-only metadata and show them in references. Older records remain unknown;
missing counts are not silently reconstructed as zero. No model call or extra
validation/repair pass is needed: the ledger uses validated source linkage.

Protected progress profiles preserve numeric `extracted_records`,
`merged_duplicates` and `omitted_stories` alongside the existing coverage counters.
They correspond to the source-mix fields above. No source names, passage text,
mailbox IDs, rejected drafts or exception content enter this log protocol.

Sparse editions may be brief; large editions still aim below 27 minutes and must
remain within the 30-minute delivery ceiling. Do not expand a script merely to
fill a target duration or treat a shorter edition as a failure without looking
at the available evidence. Original-source approval and publication verification
remain mandatory.

Review packets share an excerpt only when its original text, source group and
attribution are identical. Each fact still points to its complete supporting
text. Overlapping prose, different source messages and different articles are
not merged. Within the packet, `fact_support` points to the first validated
identical excerpt, not to a shortened or newly summarized passage.
Original evidence IDs and hashes remain in the private manifest;
integrity, collected-source checks and the review-size bound run before this
packet-only compaction. This adds no model call and removes no factual review.
A smaller packet is not proof of a faster model response; measure ordinary
deliveries before claiming time savings.

### Investigating a coverage gap

Follow the pipeline in order instead of treating every gap as a Gmail failure:

1. Confirm the exact label, episode date and schedule timezone privately. A
   schedule's display name need not equal its episode run name. Inspect the
   parameters that produced the run, not just a later edited schedule.
2. Compare collection counts with the original messages, if they remain
   available. Inspect substantive body text and the 50,000-character per-body
   prompt cap. Re-reading a matching count is useful evidence, not proof that
   labels, bodies and mailbox processing state are unchanged since the run.
3. Inspect extraction and consolidation. A configured desk can legitimately
   exclude unrelated reporting; promotional content may have no useful news.
   These are possible explanations, not conclusions from a source count alone.
   Consolidation must retain every supporting source's lineage.
4. Compare available news with selected news. If no news candidates were omitted,
   the audio shortlist did not drop a candidate. A missing newsletter then lacks
   representation in the retained news evidence, not necessarily in collection.
5. Use the original run's private manifest to identify missing sources when it
   exists. Published aggregate metrics and reference links do not retain that
   mapping. Do not guess missing identities from weak vocabulary overlap, fetch
   unrelated mailbox messages or persist originals in public diagnostics.

If the original mapping is gone, report the uncertainty. The coverage ledger
distinguishes extraction from selection but cannot explain omissions on its own.
New runs can carry the optional fixed-category accounting described above; these
measurements do not reconstruct old runs or establish an editorial reason as a
source-proven fact. Never manufacture stories or add unbounded correction calls
to obtain an explanation.

## Listening and script review

1. Compare a few lead stories with their original supporting passages. Check
   names, units, dates, attribution and qualifiers, including translated material.
   Do not infer accuracy just from successful delivery or a model approval.
2. Read and listen to several adjacent host turns. Does the next turn add a new
   supported detail or answer a useful question? Distinguish natural thematic
   words from an unnecessary second announcement of the section heading.
3. Check exact repetition, then semantic repetition. An exact-sentence count of
   zero cannot detect the same news paraphrased twice or a missing material fact.
4. Prefer direct spoken wording to abstract business or analytical labels. Keep
   a reported analyst's interpretation attributed; do not turn it into an asserted
   fact or invent a simpler explanation unsupported by the original source.
5. Inspect the closing separately: catalog quotation and attribution unchanged,
   source retained, followed by at most one original 25-word observation. It should
   connect one safe news story with brief wit, not retell the story or joke about
   victims. Quote-only is allowed when no useful safe observation is available.
6. Listen to names, sentence boundaries and handoffs. Text length and contraction
   counts cannot establish natural delivery or correct pronunciation. English-voice
   name overrides are approximations, not multilingual speech guarantees.

Style observations should guide the existing draft, not create extra model passes,
arbitrary rejection quotas or weaker factual checks. For a historical discrepancy,
first confirm which source revision produced that run; newer fixes do not rewrite
already-published recordings.

### Proportionate brief openings

When the existing evidence-sized script ceiling is at most 1,300 words, new drafts
use an application-owned opening: each active host introduces themselves, and one
host states the program, episode date and producer credit. This threshold follows
the evidence budget, not a Gmail label or the number of received messages alone.
The original AI disclosure remains intact as a separate spoken block. All news
facts, qualifiers and attribution belong in their selected sections; an agenda or
news preview is not needed in the opening. Broader editions keep their editorial
introduction. The trusted quotation, optional short comment, coverage, full factual
review, source references and duration ceiling are unchanged.

This is assembled before factual review, not a post-verification trim or additional
model call. Exact news-turn patches preserve the existing introduction. Full repairs
of a newly compact edition retain the same compact opening; a legacy repair draft
with a different valid introduction keeps its existing behavior so introductory
reporting is not silently deleted. Ordinary deliveries still need listening checks:
word count is not a promised number of seconds, and concise copy alone does not
establish naturalness or factual coverage. No historic audio is regenerated.

## Newspaper and runtime

If the newspaper is disabled, its `skipped` state is intentional and says nothing
about PDF quality. When enabled, review the independent edition for complete
sentences, distinct developments, useful supported visuals and readable two-page
layout; a third page is reserved for substantive overflow. Do not use the podcast
script as a fallback newspaper or present a missing paper as successful.

Check the artifact's generation date against renderer changes before assigning
a historical layout defect to the current code. Review every page visually:
page bounds alone do not detect text collisions, excessive density, tiny body
type or an unnecessary mostly empty third page. A logo/image count does not
prove that the reporting contains useful illustrations. Likewise, exact-sentence
deduplication cannot detect repeated implications or several restatements of
one development across the headline, lead, standfirst and executive panel.
Avoid joining unrelated stories under one implied causal narrative. Prefer a
supported comparison, timeline or process visual when it adds something beyond
another set of headline cards; never invent values to fill a chart.

For delivered audio, measure the actual encoded file rather than extrapolating
from cached voice previews. Compare integrated loudness and true peak with the
configured targets; inspect loudness range and long pauses separately. Short
host-level samples can reveal imbalance but do not represent the whole voice
track. Strictly increasing transcript timestamps inside the recording establish
timeline consistency, not audible sentence alignment or natural pronunciation.
Keep technical conformance and subjective listening findings separate.

Keep failed attempts in timing analysis and separate generation time from workflow
time and account-billed minutes. A small successful sample confirms adoption,
not a lasting reduction in retries or provider usage. Any proposed optimization
must preserve original-source review, no-spend controls, publication reserve and
the 60-minute workflow cap.

## Structured-draft failure prevention

The script example is built from the run's exact hosts, nonempty desks and required
story IDs. It contains no fictitious public URL; empty show notes are valid. Its
placeholder narration is a shape example, never news or a factual fallback.
Extraction and script repair instructions are separate: an extraction repair must
not accidentally request dialogue. The existing one-correction limit is unchanged.

Dialogue arrays, required fields, section names/duplicates, desk-specific coverage,
turn length, source evidence and unsupported reference URLs have fixed diagnostic
codes. These categories may be logged; source snippets, IDs and exception text may
not. Unknown diagnostics remain `model_structure`, never arbitrary model text.
Ambiguous duplicate JSON keys and non-finite JSON constants are rejected.

In two-host conversation mode only, an overlong turn can be split at existing
sentence boundaries with its original words, order and speaker preserved. A single
overlong sentence still requires correction; no sentence is cropped and no fact is
removed to meet the limit. Solo/broadcast prose is not reformatted this way. All
retained output still passes independent original-source factual review before audio.

These safeguards reduce preventable contract failures; they cannot guarantee that
an external model always returns valid, supported content. Do not claim an exact
cause for a historical `model_structure` failure when its private draft is absent.

### Application-owned podcast references

Drafts and factual repairs return `show_notes: []`. Before parsing a generated
script, discard any model-supplied show notes, including malformed legacy values.
After validating coverage and story IDs, build references in first-citation order
from those story records, deduplicate normalized public URLs, and append the
approved quotation attribution. A newsletter-only story receives a descriptive
entry without an invented link or mailbox identifier. Unused stories are excluded.
The saved script schema and player/feed reference format remain unchanged.

This assembly adds no model calls and does not approve spoken claims. Unknown
story IDs, altered source lineage, and factual failures still block publication.
Regression checks cover a repaired draft reintroducing an unsupported URL, a
newsletter without a public link, tracking/credential removal, duplicate links,
and legitimate source URLs ending with punctuation.

### Bounded factual script repairs

After a rejected factual review, the existing repair call can return exact text
replacements for existing news turns instead of returning the complete script.
At most twelve unique targets are accepted. Each uses zero-based section/turn
indexes and the exact current text as an anchor; stale anchors, unknown fields,
mixed script/patch responses, no-op edits and invalid batches are rejected
atomically. Hosts, story IDs, source lineage, headings, introduction, conclusion
and trusted closing cannot be changed through a patch. Conversation replacements
retain the existing 115-word ceiling without cropping or silently splitting them.

Issues requiring broader changes use the complete-script response in the same
already-budgeted call. The existing single structural retry and configured factual
repair limit remain unchanged: there is no extra fallback call, model pass or
automatic episode retry. The complete reconstructed script still passes the same
structural, coverage, host, quotation and episode-length constraints, followed by
the unchanged independent factual review against original source passages. Text
anchors establish the target only; they are not proof of a claim. Application-owned
word counts and source notes are recomputed, not model-controlled.

Cloud profiles count structurally accepted patch and full-script repair responses;
local output also shows the edit count. These counters do not establish factual
approval and contain no private text or anchors. Validate adoption and timing in ordinary deliveries;
smaller response size does not establish model latency, reduced billed minutes,
or unchanged subjective audio quality. No synthesis or voice setting changed.
