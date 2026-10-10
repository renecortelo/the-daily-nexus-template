# Trust and polish implementation plan

This plan improves the existing per-user product without a provider migration,
paid APIs, extra model-review passes, a shared multi-user service, or a broad UI
redesign. Production credentials and deployment state must never enter the
reusable public repository. Functional fixes must reach both repositories through
a reviewed, allowlisted export.

## Non-negotiable release controls

- Keep `useG1Credits=false`, telemetry disabled, and provider no-spend controls.
- Keep the protected runner budget and 60-minute workflow timeout.
- Do not restart failed editions or change saved schedules unless requested.
- Never weaken factual safety or publication verification to save compute.
- Test locally first; do not generate full episodes merely to test UI changes.
- Keep each increment small, reversible, tested and separately described.
- Public scans cover both current files and history; logs and fixtures use only
  synthetic identities and links.

## 1. Privacy boundaries, session semantics and data minimization

Status: in progress.

First increment implemented on 2026-10-09: accurate disclosures, original-sign-in
console deadlines, logout cleanup, favorite retention controls, model source
aliases and recognized URL/email minimization. Local regression gates: 301 Python
tests, 20 browser behavior tests, 9 clock tests, and Ruff. The server authorization
gate below remains open; this increment does not claim complete token revocation
or migrate historical cloud records.

First increment:

- Replace misleading cloud/local and browser-close claims with accurate wording.
- Base the console's absolute deadline on Firebase's original authentication
  time, not page load or token refresh; preserve idle state across reloads.
- Clear sensitive form and favorite-picker state immediately on logout, and
  prevent late authorization responses from reopening the console.
- Make persistent favorites an explicit device choice while preserving existing
  saved favorites and providing a session-only alternative.
- Replace mailbox identifiers with per-request source aliases in model payloads,
  restoring internal source linkage after validated extraction.
- Remove recognized personal/tracking parameters and credential-bearing links;
  redact email/contact utility content conservatively without discarding news.
- Add regressions and explain the exact boundaries in setup/security docs.

Acceptance: page reload and token refresh cannot extend the console deadline;
expired or unverified authentication cannot open private views; logout empties
private controls; generated reference fixtures retain functional article query
arguments but no recognized personalization/authentication values; original
mailbox IDs and personal email addresses do not appear in source prompts.

Authorization migration implemented/tested on 2026-10-10, but activation requires
an explicit operator step: [RUNNER_IDENTITY.md](RUNNER_IDENTITY.md). A dedicated
Firebase Anonymous identity receives an exact owner grant; it cannot change
owner records, schedules, clock projections, or its own authorization. Active or
revoked grants enforce the browser's original-sign-in one-hour limit in Firestore,
including the owner-document check used by Cloud Clock. Automation tokens are
separate and not subject to this browser age limit. Existing alarms are unchanged.

Prepared/absent grants deliberately preserve the legacy runner until the private
encrypted secret is replaced. The explicit migration checks for an idle runner,
secures the token locally, verifies a bounded schedule read, replaces only that
GitHub secret, checks activity again and activates the grant. It never runs during
generation. Resuming requires the exact prepared identity; active/revoked grants
cannot downgrade to prepared through client rules. No service account, model
call, paid service, feed rotation or episode generation is added. Authentication
adds one authorization-document read per new runner client; rule document checks
can consume Firestore reads. Do not describe this as zero compute/read overhead.

Local regression coverage includes actual Firestore emulator rules, old/refreshed
browser tokens, unknown/cross-owner identities, restricted automation operations,
revocation and secret-install failures. Production activation is NOT implied by
these tests or a rules deployment. Idle expiry/logout remain browser-local and do
not immediately revoke copied tokens. Separately reviewed feed-link rotation
remains open; no working feed rotates automatically.

Credential/file increment implemented on 2026-10-10 without identity activation:
private file paths reject linked ancestors/junctions and hard-link aliases;
bounded descriptor reads validate regular files and POSIX ownership/access.
Atomic writes establish 0700 directories and 0600 files before writing on Linux;
permission failure is explicit. Windows uses its existing ACL/vault boundary,
not a new cross-platform ACL guarantee. Relative credential paths retain aliases
until this validation instead of resolving them away during configuration load.
Antigravity's request and bundled agent files use the protected writer. Requests
have a separate 16 MiB envelope limit, with no cropping; credential reads/writes
keep their 2 MiB limit. Recognized unrelated service credentials are excluded from
the model subprocess environment; the OAuth/keyring transport is unchanged.
Preparation and cleanup validate the same fixed private Actions paths, reject
broad/linked targets and report incomplete cleanup. No provider call, new model
pass, browser change, credential rotation, extra generation or runtime-cap change
is added. Same-user compromise, unknown environment secrets, CLI-managed state and
secure disk erasure remain outside this boundary. Real iOS reading/accessibility
and production adoption of these file controls remain open.

## 2. Original-source verification and source lineage

Status: implemented on 2026-10-09; production adoption is evaluated on subsequent
runs, without generating extra editions for testing.

Carry precise supporting excerpts for important claims; validate source and URL
membership; use those excerpts in the existing factual-review calls. Preserve
attribution, quantities, dates, translated names and uncertainty. Show coverage
as collected/extracted/selected/duplicate/omitted, not a guarantee of accuracy.

Acceptance: intentionally corrupted facts or forged links fail even when the
script repeats an extracted record faithfully; supported multilingual reporting
survives; no additional paid service or routine review call is introduced.

Extraction now supplies one or two original-passage references for every fact.
The runtime resolves those references itself, retains adjacent qualifications,
rejects unrelated links and sources, and preserves supports during consolidation.
The existing script and newspaper reviewers receive deduplicated original text;
repair drafts receive the same evidence. Factual approval must be explicit.
Numeric lineage counts reach the owner console; original excerpts do not.

Limitations: regression reviewers are simulated, not an independent truth oracle.
The original reporting itself may be wrong, and model review can still err.
Prompt text increases, although regular call count and runtime caps do not;
there is no claim of measured production speed improvement. Excerpt packets are
bounded and fail explicitly rather than silently clipping missing context.
Saved originals remain in private local/run manifests, not the public template
or Hosting release. Historical manifests without them cannot be used to write
a new independently verified newspaper; existing media/rendering remain usable.

## 3. Archive lifecycle and queue recovery

Status: implemented on 2026-10-09; normal future publications reconcile existing
archive records. No historical media cleanup or extra generation was triggered.

Define retention in episodes or days explicitly; synchronize available/retired
metadata with audio, paper and preview assets. Distinguish enqueue success from
wake-up success; retry a wake without creating another generation request. Keep
intentional distinct editions selectable and failed executions terminal.

Acceptance: no selectable available record points to retired media; wake failure
cannot cause accidental duplicate production; intentional reruns remain possible.

`retention_episodes` names the count explicitly (1–30; default 30); the legacy
`retention_days` alias still works without changing saved deployment values.
The current publication is retained, even for a historical date, alongside the
newest prior dated editions. Retirement removes audio, PDF and up to three
previews from the new Hosting release. Remote verification compares the exact
retained GUID set in its existing feed fetch, before owner archive metadata is
reconciled. Published history and numbering are preserved; `mediaState=retired`
makes a record non-selectable. Cached selections require a fresh owner-server
check, with logout and stale-response guards.

Queued items have WAKE RUNNER: dispatch retry only, never a new generation.
REQUEUE remains an explicit fresh attempt for terminal failed/expired requests.
Concurrent submits/wakes are guarded, and enqueue success is distinguished from
accepted dispatch and actual runner start. A confirmed feed plus failed metadata
synchronization is still terminal success, with a synchronization warning; the
next successful publication retries reconciliation, not production.

Limits: Hosting and Firestore cannot change atomically. A network failure can
leave a temporary metadata mismatch; reconciliation converges on the next normal
publication. Previously orphaned assets absent from the live feed require a
separate, explicitly reviewed inventory cleanup. The release does not reclaim
older Hosting versions, alter schedules, requeue failures or raise runtime caps.

## 4. Free-tier visibility and measured efficiency

Status: measurement and owner-console visibility implemented on 2026-10-09.
Provider-wide quota integration and measured runtime optimizations remain open.

Expose runner time, queue delay and projected allowance headroom. Measure before
optimizing model calls, synthesis and safe non-private caching. Account for
Hosting release storage, paper previews and transfer separately. Explain quota
deferral and missed ready-by targets instead of retrying indefinitely.

Acceptance: no paid fallback, runtime-cap relaxation or unbounded retry; measured
before/after results; factual checks and private production isolation remain.

The owner monitor now exposes the latest terminal stage/operation measurements,
start delay, preparation time, observed job time and scheduled ready-by outcome.
One bounded owner document retains at most 20 sanitized task samples, including
unsuccessful attempts. A seven-day scheduled-generation reference requires at
least three completed samples and is explicitly not a remaining allowance.
The existing final-profile write gains one owner-only read; the console gains
one document subscription and a read on explicit refresh. There is no new
polling job, model call, account token or dispatch retry.

Publication exposes measured new audio/PDF/preview sizes, staged file bytes and
retained feed audio sizes (remote sizes are declared by the verified RSS). These
are not total Hosting usage: remote paper sizes, retained older releases and
actual transfer remain unknown. Missing measurements are omitted, not shown as
zero. Measurement failures never invalidate publication or restart generation.

Account-wide Actions allowance and Firebase storage/transfer headroom remain
explicitly unknown, with links to the owner provider consoles. Repository task
duration alone cannot account for other repositories/workflows, setup/cleanup,
platform billing rules or exhausted provider quotas. No quota-based automatic
deferral or speed improvement is claimed. A read-only production baseline was
reviewed privately; generation dominated setup, so dependency changes were not
made merely to claim a performance gain. Evaluate the next normal runs before
making a separately tested optimization.

Draft-error prevention increment implemented on 2026-10-09: script JSON examples
now use JSON-escaped disclosure text and actual configured hosts, including both
introductions for a two-host edition. A deterministic request-local section plan
maps required story IDs to nonempty ordered sections and exposes the existing
conversation turn limit. Newspaper examples show three distinct executive labels
and one concrete visual kind instead of literal strings of alternatives. Existing
takeaway/label limits are stated before drafting; small evidence sets are not
given the broad-edition prose target. Validators, original-source review, repair
caps, duration ceilings and paid-service guards are unchanged. No extra review
call or publication is introduced. The added plan increases prompt text slightly;
fewer retries and faster cloud runs are hypotheses to evaluate on normal future
runs, not demonstrated savings. A short offline synthesis candidate was tested
and rejected because it did not show a credible speed gain; production synthesis
was not changed.

## 5. Podcast and newspaper quality

Status: first audio-safety/measurement increment implemented on 2026-10-09.
Subjective listening changes and newspaper-design work remain open.

Give lead developments depth and supporting briefs brevity. Use short A/B audio
samples for spoken syntax, handoffs, names and pauses; measure final encoded
loudness and peaks. Remove newspaper semantic repetition and empty overflow;
use only explanatory, evidence-supported visuals, with explicit units and dates.

Acceptance: no filler duration requirement, clipped assertions or unsupported
implications; coherent two-page editions where practical; a third page only for
substantive readable overflow; pronunciation and delivery reviewed by listening.

The final encoded MP3 now receives a bounded local loudness/true-peak/range audit,
with numeric results in its private manifest. This is observation, not an extra
encode, synthesis retry or new publication gate. It skips near the protected
deadline, retains two minutes for publishing, and times out after 30 seconds;
unavailable measurements are explicit. Existing targets, voices, pauses, speed,
original transcripts and duration limits stay unchanged. Invalid/silent synthesis
samples cannot become PCM speech; only oversized waveforms are attenuated before
PCM16 would clip them. Non-finite/out-of-range loudnorm settings fail early.
Synthetic cached-voice previews and local measurements are used, with no provider
request or generated production episode. This observation adds bounded decode
work; it is not a claim of free compute or measured cloud speed improvement.
English-voice pronunciation overrides remain approximations; listening feedback
is required before claiming more natural delivery or choosing different voices.

Measured volume increment implemented on 2026-10-09: `balanced_speech_v1` uses
moderate speech compression before the existing loudness filter, a 0.5 dB offset
and two-dB pre-codec peak margin (clamped to the supported filter range). Concat
and streamed-WAV fallback share this exact chain. It changes amplitude dynamics
only: there is no new pass, model call, quality retry, voice change, tempo/pitch
change, transcript edit or bitrate increase. The existing final observer keeps
the configured delivery targets; a fixed profile tag records adoption privately,
not a claim that an unavailable measurement passed. The deadline and publishing
reserve are unchanged. Twelve short offline clips across seven cached voices and
mixed hosts improved from 5/12 to 11/12 meeting both targets; all processed peaks
were below the ceiling. One alternative voice remains slightly quiet. A two-pass
candidate was rejected because it added work without consistently solving peaks.
Production overhead, subjective listening and naturalness remain unverified.

Listening/preparation increment implemented on 2026-10-10: the existing script
request now prioritizes subject/action before dense figures, explicit references,
direct answers and distinct host contributions, without templated takeaways or
forced handoffs. Solo, broadcast and conversation instructions remain distinct.
These are drafting preferences, not new subjective rejection quotas; factual
approval, original evidence, coverage and exact quotations remain mandatory.
Version expansion can no longer rewrite existing phoneme-annotation labels.
Terminal questions inside closing quotes/brackets use the existing short response
gap, including curly apostrophes/guillemets; heading/phase priorities and existing
silence subtraction remain. No new pronunciation entries, voice, pitch, synthesis
speed, extra model pass, paid service or deadline change is introduced. Regression
tests use fictional examples; no production edition is generated for testing.
The existing cached pronunciation comparisons can be auditioned locally. There
is no claim of measured improvement in accent, emotion or perceived naturalness;
subjective listening feedback and newspaper-design work remain open.

Closing reliability increment implemented on 2026-10-10: the application inserts
the reserved catalog quotation, author and public reference deterministically;
the model supplies only an optional original closing_comment turn. Saved scripts
retain sign_off for desktop, cloud, audio and transcript compatibility. Legacy
drafts may keep a clearly separate observation; an ambiguous paraphrased quote is
discarded rather than misattributed or presented as original copy. The existing
complete-sentence 25-word guard also drops a repeated approved quotation.
Host configuration, editor credit, coverage, duration ceilings, unsupported-URL
rejection and original-source factual review remain enforced. No extra model
call, provider, generation retry loop or runtime-budget change is introduced.
Fixed allowlisted diagnostics distinguish quote text, author, source, editor
credit and comment shape without preserving rejected text or private identifiers.
Production adoption and any retry/time savings remain unmeasured.

Newspaper rendering increment implemented on 2026-10-10: visual panels measure
and wrap complete values, labels, details, titles and captions before allocating
page space. All six accepted visual items survive; negative bars extend left of
a shared zero and zero has no filled extent. Invalid/nonfinite numeric magnitudes
fail validation. The renderer no longer generates repeated article excerpts as
secondary briefs or teaser text. Optional navigation repeats titles only.
First-page spare space can carry complete additional articles, preserving order
and leaving reporting on page two. Article/card padding and text widths now match
their drawing measurements. Article body floors rise to 8.1 pt for features and
7.7 pt for compact columns; executive copy and visual detail are larger too.
Three pages remain the maximum; oversized complete panels fail explicitly rather
than silently clipping copy or skipping briefs. The optional-paper fallback still
allows a verified podcast to publish; no new model call or rendering repair loop.

Automatic prose/bullet deduplication now removes only exact normalized sentence
copies, including within an article. Similar word sets can describe different
numbers, conditions or reversed actors; they are preserved for the existing
original-source/semantic reviewer. This is not a guarantee of zero semantic
repetition. Factual gates, newspaper generation settings, two-page target,
runner deadline and cost controls remain. A fictional two-page proof was rendered
and visually reviewed locally; no mail, production edition or feed was accessed.
Generated proof files stay excluded from both repositories. Semantic quality on
real future editions and accessibility/mobile UX remain to be evaluated.

## 6. Mobile accessibility, UX and maintainability

Status: interaction/accessibility, semantic reading, shared parameter-form and
incremental monitor increments implemented on 2026-10-10; remaining module consolidation and real Safari/PWA,
VoiceOver and software-keyboard testing remain open.

Incremental monitor implemented on 2026-10-10: rows are reconciled by immutable
Firestore document ID. Identical snapshots keep the same text nodes, cards and
action buttons; only changed copy, action kinds and list membership/order are
updated. The existing local second timer updates queued/running clocks only;
it does not recreate rows, sort requests or read the cloud. Clock rendering
pauses in hidden tabs; session-expiry checking remains active. Sorting preserves
button focus; removed actions return focus to the retained row, or the list if
the row disappears. Filters and scroll behavior keep their existing semantics.
Manual refresh records its client refresh time before rendering the runner,
without changing the runner's distinct cloud-check timestamp.

The row map contains only visible requests from the existing latest-100 window,
is pruned when rows disappear, and is cleared with private filters at logout.
Late unauthorized snapshots cannot refill it. DOM text remains inert; no HTML
insertion, persistent storage, new subscription, provider, model pass, schedule
change or generation is introduced. Six focused regressions bring browser
coverage to 54 tests. Synthetic Chromium at 320/390/768/1280 verifies 100 rows,
20 identical snapshots with zero list DOM mutations, text-only clock ticks,
selection/focus, filters, status transitions, logout and bounded controls.
This is a local interaction result, not a measured improvement in generation
runtime. The shell advances to 4.1.13 / cache v4-43. Further module consolidation
and real iOS/PWA/assistive-technology testing remain open.

Shared parameter increment implemented on 2026-10-10: GEN, SCHED and favorites
use one explicit field allowlist, serializer, hydration/preflight and control
synchronizer. Temporarily disabled voice, tone, dialogue and newspaper-scale
preferences remain configured instead of being lost through FormData omission.
Host gender restrictions, distinct duo voices and automatic web publication remain.
Missing legacy fields reset to markup/default values, not a previously selected
record's mailbox or editorial preferences. Missing mailbox/run-name values remain
empty until entered (schedule run-name retains its existing schedule-name fallback).
Malformed sections/select values report persistent errors before replacing edits.
Dates, timing, weekdays, enabled state and record identity are separate from the
parameter allowlist; editing a schedule retains its existing explicit handling.
Initialization is idempotent. No saved records are migrated or silently updated;
there is no layout redesign, additional storage, cloud read, model call, schedule
change, provider or runtime-limit change. Six focused browser regressions bring
the suite to 48. The shell advances to 4.1.12 / cache v4-42. Incremental monitor
rendering was completed in the following increment; further module consolidation remains open.

Reading increment implemented on 2026-10-10: newly published ready newspapers
include a bounded, allowlisted reading projection in the existing owner archive
document. It comes from the final independent newspaper artifact, not the audio
script, raw newsletters or a new model request. Internal source/story identifiers,
source passages, local paths and URLs are not projected. Missing, oversized,
invalid or recognized legacy script-derived artifacts retain the page/PDF view;
there is no automatic historical regeneration or backfill.

READABLE reflows complete copy with semantic headings, paragraphs, lists, exact
bold highlights and visual facts expressed as labeled text. PAGES preserves the
original graphics; the PDF itself is unchanged. Reader zoom scales text without
horizontal scrolling. View choice is session-only, and logout/retirement clear
content and invalidate pending callbacks. Text view does not request preview
images. Both views are populated only after the existing fresh owner-server
availability check. New publication metadata records exact preview URLs; legacy
records keep same-file page fallback. The owner record's update timestamp provides
a stable cache revision, replacing per-selection random cache busters while
allowing corrected editions to refresh. A failed first preview cannot destroy
successful later pages.

No additional cloud read/subscription, generation, model pass, provider, schedule
change or runtime-cap change is added. Bounded metadata increases existing archive
transfer/storage and the publication writes slightly; this is not zero resource
overhead. Private reading content is not stored in local/session storage or the
service-worker offline cache. Existing Hosting media remain bearer-link resources,
and logout does not guarantee browser-memory/disk erasure or revoke their URLs.
The shell advances to 4.1.11 / cache v4-41. Real Safari/PWA, VoiceOver and production
adoption on normal future papers remain to be evaluated; maintainability
consolidation is still open.

Both section editors now offer earlier/later buttons and Alt+Left/Right,
retain focus after moving, return focus to the input after removal and announce
position changes without reannouncing the complete chip list. Wrapped-row drops
support the middle and end; foreign dropped text cannot create a new section.
The text entry has an explicit associated label; invalid additions retain existing
values. Logout clears both private values and the last ordering announcement.

Mobile/coarse-pointer buttons use 44-pixel minimum targets at the default font
size. Progress hit areas are larger while painted tracks stay thin. Shared focus
outlines include links and transcript/ordering controls; weekday focus is visible.
Browser scroll insets follow the sticky navigation/player height. Small-screen
pickers use 16-pixel text to avoid the usual input-focus zoom; narrow weekday
and About layouts no longer depend on a wide minimum column. Compact Pause/Resume
labels and both timeline value descriptions stay synchronized. Mode changes
respect reduced motion. Errors persist until dismissed or replaced, obsolete
notice timers cannot hide newer errors, and logout clears the message/timer.

Twenty-nine existing behavior tests were supplemented with eight focused regressions
in the browser suite (37 total); local synthetic Chromium checks cover 320, 390,
768 and 1280 pixels across all five views, including keyboard reordering,
touch sizes, overflow, real painted track height and reduced motion. These do
not certify real iOS, assistive technology or every production archive state.
No provider calls, generation, scheduler change, additional cloud read, private
offline cache or new dependency is introduced. The static shell advances to
4.1.10 / cache v4-40 and must be deployed preserving clock and media assets.

Retain the retrofuturist identity; improve invisible touch targets, non-drag
section ordering, focus, readable text and persistent errors. Add semantic mobile
reading from approved issue data and exact, stable preview manifests. Share form
logic; update only changing monitor fields; consolidate modules incrementally.

Acceptance: real Safari/PWA, keyboard, screen-reader, zoom, reduced-motion and
software-keyboard tests; no private offline caching by default; one build
identity; public and private functionality synchronized with sanitized export.

## Release procedure for each increment

1. Review the diff and add focused regressions.
2. Run Python, browser-behavior, clock, lint and syntax checks locally.
3. Export only approved source/docs/tests to the public template, retaining its
   generic defaults and deployment-free history.
4. Scan current public source, complete history and known local private values.
5. Commit/push each repository through the normal approval controls.
6. Deploy only the relevant app assets, preserving the existing feed, clock,
   authentication configuration and schedules; verify the hosted build.
7. Record what shipped and what remains open. Never present a UI timeout or a
   successful secret scan as complete server-side security certification.
