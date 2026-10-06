# Editorial scope and closing quotations

## DATA is an IT desk

The section name `Data` (including `DATA` and other capitalization) refers to
data engineering, platforms, databases, architecture, ETL/ELT pipelines,
storage, processing, quality, governance, and analytics/BI tools. Numbers in
an economic, political, sports, or other story do not make it a DATA story.
Quantitative evidence about a genuine data-platform development still belongs
in DATA.

This shared definition accompanies extraction, script drafting, newspaper
drafting, and both existing editorial reviews. A desk is not filled with
unrelated stories just to avoid being empty. Its visible name, order, and
saved schedule parameters remain unchanged. This is an editorial instruction
and review rule, not a claim of infallible automated classification.

## Closing quotations

The checked-in catalog has 43 quotations with public source links and topic
tags. New entries were checked against the linked editions; short excerpts
retain their wording, and translation attribution is retained where supplied.
The catalog is curated during development, never fetched or expanded during
a scheduled generation.

The selector runs locally, with no additional model call:

1. Exclude quotations identifiable in the latest 30 published episodes of the
   user, across all schedules. Cloud runs query the owner-only archive ordered
   by publication update time, not the first page of historic episode dates.
2. Prefer authors not used in the latest three identifiable quotations when
   another author remains available.
3. Score remaining quotations against topics in the extracted newsletter
   stories. TIH does not displace the actual news when selecting topics.
4. Break ties reproducibly using the episode date and immutable execution key.
5. Reserve the selection in the execution's local database. Script corrections
   retain it. The final manifest and owner-only episode metadata retain its ID.

New metadata uses an ID derived from quotation text and author, not from any
account identity. Legacy episodes are recognized through unique catalog source
links. A book URL shared by several quotations is not treated as one unique
quotation; ambiguous or missing historic references cannot provide a reliable
exclusion. The default catalog is larger than the exclusion window. A user's
smaller custom catalog falls back to its least recently used quotation instead
of making the episode fail because the catalog is exhausted.

After quoting and attributing the selected text, the host adds one brief
original observation tied to a specific non-TIH story covered in the episode.
It must not invent facts, imply the original author commented on today's news,
repeat the story summary, or default to an unrelated stock joke. Humor is
optional and must respect sensitive news. The existing factual review checks
this closing observation; no new review stage or external service is added.

These changes affect future generations only. They do not regenerate existing
episodes, change authentication or publishing, increase the one-hour cloud
runner limit, or resume failed occurrences.
