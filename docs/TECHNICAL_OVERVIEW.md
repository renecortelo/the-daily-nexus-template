# Technical overview

The Daily Nexus is a privacy-oriented newsletter-to-podcast and
newsletter-to-newspaper pipeline. It can run manually on Windows or unattended
on an ephemeral Linux runner. The web application is a control, playback, and
reading surface; it does not perform model inference in the browser.

## System architecture

![Operator inputs feed a Python generation runtime on a Windows desktop or an ephemeral GitHub-hosted Linux machine: collect and sanitize, Antigravity calls, quality gates, then Kokoro and FFmpeg for audio and ReportLab and PyMuPDF for the newspaper. A per-operator deployment can add a Cloudflare clock, an Actions workflow in a private repository, owner-locked Firestore data, and Firebase Hosting.](../assets/diagram-architecture.svg)

The deployment boundary is described precisely below: an unattended run uses
a GitHub-hosted machine and owner-locked cloud services, so it is not a purely
local data path.

Every deployment belongs to one operator. Sharing the source means another
operator creates a separate private repository, credentials, Firebase project,
and Cloudflare Worker; it does not mean adding users to an existing production
deployment.

## The eight-stage generation run

| Stage | Process | Main output or control |
| --- | --- | --- |
| 1 | Query Gmail for the exact label and selected date; optionally load date-specific research | Approved newsletter bodies and evidence records |
| 2 | Decode supported tracking wrappers inside the runtime, reject utility links, enforce HTTPS/public-address checks, check robots rules for the original URL and every redirect destination, and retrieve readable public pages | Newsletter-first evidence enriched with safe public text |
| 3 | Ask Antigravity for structured story extraction, classification and ranking; resolve original-passage references and consolidate duplicates locally | Evidence-linked story records |
| 4 | Generate the configured solo or two-host podcast script with ordered or content-derived sections | Structured host dialogue and show notes |
| 5 | Check script claims against original source excerpts, not just extraction summaries; repair rejected drafts within bounded attempts | Source-consistency-reviewed spoken script |
| 6 | If requested, generate and quality-check an independent reader-facing newspaper, targeting two pages with a third page allowed only for readable overflow | Structured newspaper JSON, PDF, and page previews, or an explicit skipped status |
| 7 | Synthesize each host with Kokoro inside the selected private runtime, assemble segments, preserve transcript timing, and normalize the MP3 with FFmpeg | MP3 and timed transcript |
| 8 | Finalize locally or publish an incremental Firebase Hosting release, then fetch the remote RSS feed and verify the new episode | Local archive or remotely verified private feed |

Temporary source payloads are removed in the pipeline's cleanup path. A run is
not marked published merely because files were uploaded: remote RSS verification
must also succeed.

Each extracted fact cites one or two passage IDs from collected newsletters or
retrieved pages. The runtime copies the actual source text and adjacent context,
validates source/URL membership, and remaps supports when merging duplicates.
The existing script and newspaper review calls receive deduplicated original
passages plus a fact-to-passage map; repair drafts receive the same evidence.
Original-source factual approval must be explicit. This is a consistency check,
not independent proof that a publisher is correct; translation, chronology,
units, qualifications and attribution still require fallible model judgment.
Sources are untrusted data, never instructions for the reviewer.

Unique review excerpts are capped at 120,000 characters; oversized packets fail
explicitly rather than silently removing evidence. Regular review-call count is
unchanged, but the added source text can increase latency. Original excerpts and
integrity hashes are stored only in the private runtime manifest, not Firebase
archive metadata or Hosting files. Hashes detect corruption, not authenticity
against a malicious editor. Temporary input payloads are removed; saved manifest
excerpts follow the private archive's retention. Legacy manifests without saved
originals cannot independently rebuild a newspaper without recollecting sources;
existing PDF rendering and playback are unaffected.

The owner console's lineage ledger distinguishes extracted records, merged
duplicates, consolidated stories, selected/omitted audio stories, unrepresented
newsletters and cited passages. These are bookkeeping counts, not accuracy
scores or a promise to include every fact from every newsletter.

GEN, SCHED and saved favorites carry `includeNewspaper` (boolean, default true
for older records). It maps to `[podcast].include_newspaper` in the runtime.
When false, stage 6 makes no newspaper model, quality-review or render calls;
publication and transcript creation proceed normally. Manifest and owner-only
archive metadata distinguish `skipped` from `failed`. No schedule is silently
changed during deployment.

When requested, the newspaper is optional at delivery, not at verification: unsupported or
unclassified rejected copy is withheld. A verified podcast can publish with a
visible newspaper-failed status. Stylistic review leftovers are tolerated only
when factual safety was explicitly approved. The final MP3 duration must match
the generated speech timeline, including gaps and headings, within two seconds
of encoding tolerance; a truncated file is not accepted just because it is long.
`episode_budget.py` sizes the spoken shortlist from newsletter count and distinct
facts. Its word ceilings are 600 / 1,300 / 2,200 / 3,000 / 3,400 for 1 / 2–3 /
4–7 / 8–15 / 16+ newsletters, further reduced when evidence is sparse. These
are planning ceilings, not mandatory durations. Coverage is source-balanced;
the newspaper still receives the complete extracted story set. A draft is not
expanded just to reach a word minimum. Kokoro's complete speech timeline and
the encoded MP3 must both remain within 30 minutes; neither is silently cut.

Non-formal speech prompts prefer contractions, plain wording and genuinely
responsive host dialogue; these are style preferences, not new failure quotas.
The application inserts the approved catalog quote, attribution and source URL
locally, rather than asking the model to copy them. The draft supplies only an
optional `closing_comment` turn; saved scripts keep the existing `sign_off` schema.
Legacy drafts retain a separately identifiable observation, not an ambiguous or
paraphrased quotation. Malformed comments, unknown speakers and unsupported
references still fail validation; the independent source review still checks the
retained observation. Fixed diagnostic codes distinguish attribution, source,
editor-credit and comment errors without exposing text or account information.
The closing quote and attribution stay verbatim. Its separate original comment
targets 12–20 words (25 maximum); a deterministic guard keeps only complete short
copy or the quotation alone, without another model call or whole-script retry.
Reviewed Spanish/Catalan public-news names have synthesis-only phoneme overrides
in `speech.py`, shared by desktop and cloud. Written names remain unchanged;
local validated overrides take precedence. These are English-voice approximations,
not native-language voice switching or automatic transliteration of unknown names.

The 2026-10-10 listening increment adds subject/action-first figure explanations,
explicit pronoun references, direct answers and distinct host contributions to the
existing draft request. Solo, broadcast and conversation remain separate styles;
no extra model pass, mandatory joke, accent simulation or new style failure quota
is added. Original-source factual approval, exact quotations and duration caps
are unchanged. These are drafting instructions, not measured listening gains.
Synthesis now protects existing phoneme annotations before expanding versions
as well as before dictionary matching. Quoted terminal questions (including curly
apostrophes and guillemets) use the existing response-gap rule; phase/heading
priorities and silence subtraction are unchanged. Only these question boundaries
may receive a different added gap; source text and displayed transcripts stay
intact, and actual rendered timings still drive playback highlighting. Cached
fictional pronunciation previews remain local and are not published or committed.
English voices still approximate Spanish/Catalan names; native pronunciation or
less robotic delivery cannot be promised without listening feedback.

Newspaper rendering measures complete visual copy before allocating the panel
and article space. All six schema-accepted items are retained, with wrapped
labels/details and larger type, rather than fixed one-line ellipses. Bar charts
use a common zero for signed values; zero is never represented by a minimum
filled bar. Boolean/nonfinite/overflowing magnitudes are rejected. Units and
comparability still depend on the original-source factual review, not the layout.
Exact sentence deduplication preserves changed quantities, qualifiers and actor
order; semantic paraphrases remain the existing reviewer’s responsibility.
The renderer no longer manufactures secondary briefs from article copy or repeats
body excerpts in teasers. Measured free first-page space can hold complete extra
articles in priority order. Body type is at least 8.1 pt for features and 7.7 pt
for compact columns; a third page handles readable article overflow, never a fourth.
Unfittable visual/brief/executive/takeaway panels raise an explicit render error
instead of dropping or clipping content. A verified podcast may still publish
without the optional paper; no automatic new review, generation or repair loop
is introduced. Synthetic PDF proofs are local ignored outputs, not deployments.

Audio quality observation measures the final decoded MP3 locally with FFmpeg's
`ebur128` filter (integrated loudness, true peak and loudness range), rather than
assuming the pre-encoding `loudnorm` target was achieved. Numeric values and fixed
status codes remain in the private run manifest; decoder paths, metadata and
diagnostics are never forwarded. The observer is capped at 30 seconds and skipped
when fewer than 150 seconds remain, protecting a two-minute publication reserve.
Missing/failed measurement is unavailable, not zero; a target warning does not
retry synthesis or block a verified podcast. No automatic second normalization,
extra model call or voice/rhythm change is introduced. This bounded local decode
adds compute time; production overhead and listening quality are not yet measured.
The configured target is compared with a +/-1 LU loudness tolerance and its true
peak ceiling; FFmpeg's summary is rounded to one decimal, not a certification.
Non-finite, empty, non-mono or silent synthesis samples are rejected before PCM
encoding. Oversized samples alone are attenuated linearly to avoid PCM clipping;
quiet speech is not boosted or dynamically compressed per host.

References: [FFmpeg loudness filters](https://ffmpeg.org/ffmpeg-filters.html#ebur128)
and [Apple podcast audio recommendations](https://podcasters.apple.com/support/893-audio-requirements).

The `balanced_speech_v1` amplitude-processing profile, added on 2026-10-09,
uses the same single encoder invocation and the same filter in the concat and
streamed-WAV fallback paths. A 2:1 speech compressor (threshold -24 dBFS, 5 ms
attack, 100 ms release, unity makeup) precedes loudnorm. The configured integrated
target remains unchanged; a modest 0.5 dB offset and a two-dB pre-codec true-peak
margin improve the measured final MP3. The pre-codec peak is clamped to loudnorm's
supported minimum of -9 dBFS; very low configured ceilings therefore receive less
margin. This changes amplitude dynamics, not voice identity, pitch, tempo, pauses,
sample rate, bitrate, source WAVs or transcript timing. There is no extra encode,
analysis pass, model request or automatic quality-correction retry. The existing
final measurement still compares against the original configured delivery target.
Its allowlisted profile tag is saved in the private manifest, including when
measurement is unavailable; the tag records adoption, not a quality guarantee.

In 12 local fictional short clips (seven cached voices, public-name/acronym
previews and mixed hosts), five original encodes met both volume and peak targets;
11 processed encodes did. All 12 processed peaks were below the configured
ceiling; one alternative male voice remained slightly quiet. The mixed-host clip
changed from -17.4 to -16.8 LUFS. These are offline objective measurements, not
production results or a subjective listening approval. Processing adds modest
encoder work; actual cloud overhead is unmeasured and the protected limits remain.

## Archive lifecycle and queue recovery

`[app].retention_episodes` is a hosted edition count, not a day count: 1–30,
default 30. The legacy `retention_days` configuration alias remains accepted;
conflicting aliases are rejected. Existing private values are not migrated or
changed. With two editions daily, a 30-edition window is roughly 15 days.
The current publication is retained even if its episode date is historical;
remaining slots use the newest prior episode dates with deterministic ties.
Local archives are not automatically deleted by this hosted retention setting.

The incremental publisher removes retired MP3/PDF paths and each of the three
possible page-preview paths from the new release. It loads a complete bounded
live-feed inventory and rejects overflow rather than silently truncating it.
The existing remote feed verification checks the exact intended retained GUID
set, without a separate feed fetch. Only after verified publication does the
runner reconcile the complete owner archive, scoped to that exact feed prefix.
It patches `mediaState`, `retiredAt` and `updatedAt`, preserving original
publication history, references, transcript and sequence identity. Retired
records are non-selectable. Opening a cached episode/PDF requires a fresh
Firestore server read and fails closed if that check is unavailable.

GEN distinguishes saving a request from waking the runner. A queued item's
WAKE RUNNER retries only the existing dispatch path; it does not write a request
or reset a terminal execution. Concurrent wake attempts share one in-flight
request. REQUEUE is restricted to failed/expired requests and creates a fresh
immutable attempt; intentional additional editions remain supported.
An accepted dispatch does not mean GitHub has assigned a runner, and a generic
wake retains the existing due-schedule/oldest-eligible-request selection logic.
Authentication changes invalidate late wake and archive-selection responses.

Hosting release and Firestore metadata updates are not atomic. If synchronization
fails after verified publication, the execution stays completed with a visible
sync-pending warning; it is never automatically regenerated. A later successful
publication repeats idempotent reconciliation. Already orphaned historical assets
and old Hosting versions are not cleaned by this change; those need a separate
reviewed inventory. No paid fallback, polling scheduler or timeout increase is added.

## What each intelligence component does

- **Antigravity CLI** handles structured editorial reasoning: story extraction,
  script drafting, script verification and repair, independent newspaper copy,
  and bounded newspaper quality review. Inputs are limited to selected evidence
  and explicit instructions. Responses must pass local schema and quality
  validation.
- **Kokoro** is the bundled neural text-to-speech engine. It runs on the desktop
  or ephemeral GitHub-hosted machine rather than through an external TTS API.
  Host voice selection is separate from editorial tone. It receives approved
  dialogue, not Gmail OAuth credentials.
- **FFmpeg and ffprobe** assemble speech segments, normalize loudness, encode the
  MP3, and inspect duration. Playback speed adjustment uses FFmpeg so pitch can
  be preserved.
- **ReportLab and PyMuPDF** render the independent newspaper PDF and browser-ready
  page previews. The current renderer uses bundled project artwork and does not
  download external images.

## Application and infrastructure

| Area | Technology | Responsibility |
| --- | --- | --- |
| Core application | Python 3.11–3.12 | Pipeline orchestration, validation, storage, publishing, and desktop UI |
| Gmail access | Google Gmail API and OAuth 2.0 | Mailbox-wide read-only grant; application query is restricted to the configured label |
| Editorial model access | Google Antigravity CLI with Google OAuth | Uses an existing Google AI Pro allowance; paid API keys and AI-credit fallback are rejected |
| Speech | Kokoro, PyTorch, and Misaki | Local or ephemeral-runner voice synthesis |
| Audio | FFmpeg and SoundFile | Assembly, encoding, loudness normalization, and playback transformations |
| Newspaper | ReportLab, Pillow, and PyMuPDF | PDF layout, bundled graphics, and page previews |
| Local state | SQLite and filesystem manifests | Run state, episode inventory, checksums, transcripts, and generated media |
| Desktop | Python Tkinter | Generation, preferences, playback, reading, authentication, and publishing controls |
| Web | HTML, CSS, and vanilla JavaScript PWA | Owner sign-in, queue/schedule controls, playback, transcript/references, and PDF reading |
| Private web data | Firebase Authentication, Firestore, and static Hosting | Owner authorization; generation/schedule parameters; queue, execution, source, reference, transcript, and episode metadata; application shell; RSS, MP3, and PDF delivery |
| Automation | GitHub Actions on Ubuntu | Ephemeral generation in a private repository using persistent encrypted secrets and a one-hour job limit |
| Scheduling | Cloudflare Worker and SQLite-backed Durable Object alarms | Stores timing projections; holds a scoped dispatch secret; accepts short-lived owner tokens; dispatches the private workflow when due |
| Quality and security | unittest, Ruff, Gitleaks, CodeQL, and Dependabot | Regression, lint, secret-history, static-analysis, and dependency-alert checks |

## Privacy and security boundaries

- Gmail authorization uses the narrowest available Gmail read scope, but that
  scope is mailbox-wide. The application enforces the selected label in code;
  deployment repository write access must therefore remain tightly controlled.
- Gmail, Antigravity, Firebase, GitHub, and Cloudflare credentials never belong
  in source control. Gmail uses operating-system credential storage,
  Antigravity uses its own keyring, and the Firebase CLI keeps its refresh token
  in per-user configuration outside the repository. Unattended grants use
  encrypted secrets in the operator's private repository.
- Encrypted GitHub Actions secrets remain stored until the operator rotates or
  deletes them. During a run, the GitHub-hosted machine temporarily handles
  selected newsletter evidence, editorial drafts, generated media, and
  materialized secret files. Cleanup removes temporary files and the workflow
  uploads no Actions artifact, while GitHub retains workflow logs according to
  repository settings.
- Antigravity calls require `useG1Credits=false` and
  `enableTelemetry=false`. Known paid model credentials, Vertex credentials,
  and Google service-account credentials cause startup to stop.
- Public article retrieval rejects non-HTTPS and non-public destinations,
  follows bounded redirects, limits response size and time, and treats an
  inaccessible article as optional enrichment rather than a reason to replace
  newsletter evidence.
- Editorial payloads replace raw mailbox IDs with request-local source aliases;
  validated extraction restores the internal source linkage for coverage and
  deduplication. Recognized email addresses, mail utility lines and personalized
  URL parameters are conservatively removed without changing the original source
  records. Credential-bearing URLs are omitted rather than fetched. This does
  not detect all personal information; selected reporting reaches Antigravity.
- The web console uses session-only Firebase persistence and locks after 15
  minutes of inactivity or one hour from the original sign-in time. Reloads and
  token refresh cannot restart that deadline. Active listening counts as activity,
  but never bypasses the one-hour limit. These browser controls do not revoke
  copied tokens server-side. Firestore authorization requires a matching owner UID.
  Logout clears private forms and session-only favorites. Device-retained
  favorites require an explicit choice; legacy saved favorites remain available
  with their retention option enabled. Console logout does not cancel schedules
  or revoke the unlisted feed/media URLs.
  Playback bookmarks store only episode IDs and numeric positions in owner-scoped
  sessionStorage, at most 20 entries, and are removed at sign-out. Media Session
  controls are feature-detected; the episode title may appear on the device's
  lock screen, but no feed/media URL is included in that metadata. This does not
  guarantee background playback in every iOS/browser version. Section navigation
  uses explicit transcript heading flags, not guesses about prose.
- Owner-locked Firestore stores the full generation/schedule parameters needed
  by the runner, queue and execution state, episode metadata, source counts,
  references, and timed transcript segments. Raw Gmail message bodies, OAuth
  secrets, local filesystem paths, and Antigravity request files are excluded.
- The Apple-compatible RSS address is a capability URL, not user authentication.
  Anyone who obtains it can fetch the feed, so it must be handled like a
  password and rotated after exposure.
- The Cloudflare Durable Object stores schedule timing and opaque IDs. Its
  Worker environment also stores a scoped, expiring GitHub dispatch token and
  temporarily receives the browser's short-lived Firebase ID token; application
  code does not persist that ID token. It does not receive Gmail labels,
  newsletter text, episode settings, model credentials, or feed media.

## Console interaction accessibility

The static console offers earlier/later buttons and keyboard shortcuts
alongside section dragging in both GEN and SCHED. Reordering preserves focus,
announces positions and rejects foreign drop text; logout clears announcements.
Mobile/coarse-pointer buttons have 44-pixel minimum targets at default text size,
and timeline hit areas remain distinct from thin painted tracks. Shared focus
outlines, sticky-height scroll insets, synchronized Pause/Resume labels and
timeline time descriptions improve keyboard navigation. Narrow-screen picker
text is 16 pixels; navigation honors reduced motion. Error messages persist until
dismissed or replaced, with stale-timer protection and sign-out cleanup.

Synthetic local Chromium checks at 320, 390, 768 and 1280 pixels exercise all five
views without provider requests. They do not establish real Safari/PWA, VoiceOver,
software-keyboard or full archive accessibility. Semantic mobile newspaper
reading and component consolidation remain open. No cloud polling, private
offline caching, authorization change or generation call is added. Hosting
deployment must retain the existing clock configuration and unlisted media.

## Owner-only resource measurements

GEN's **RUN TIME AND RESOURCES** panel shows terminal stage and operation times,
manual/scheduled start delay (including job setup), observed setup and job time,
and scheduled ready-by outcomes. The ready-by date comes from the schedule's
local occurrence, not the previous-day episode date. Operations are already
included in stage totals and must not be added again. Job observation ends before
the final workflow cleanup; it is not a provider billing measurement.

`runner/lastProfile` keeps at most 20 allowlisted task samples, including failures,
without task names, mailbox IDs, URLs, raw exception text or credentials. Its
existing write gains one owner-only history read per terminal task. The browser
uses one extra document subscription and an explicit-refresh read, not a polling
workflow. It clears measurements on logout and rejects stale-session snapshots.
The seven-day schedule reference uses the recent completed-task content mix;
it excludes setup, failed attempts, manual runs and other workflows. It cannot
prove that the monthly allowance covers those runs.

Publisher metrics separate new MP3, PDF and preview bytes from declared/measured
retained feed audio and locally staged files. Remote newspaper sizes, older
Hosting releases, actual data transfer, provider-wide Actions usage and free-tier
headroom are not measured. Unknown is distinct from zero. Provider usage-console
links do not send tokens or private feed links. No additional account-billing
credential is requested. Runtime/retention limits, cost guards, generation calls,
queue selection and terminal-failure policy remain unchanged.

Sources for these boundaries: [GitHub billing API retirement](https://github.blog/changelog/2025-09-26-product-specific-billing-apis-are-closing-down/)
and [Firebase Hosting usage](https://firebase.google.com/docs/hosting/usage-quotas-pricing).

### Preventing avoidable drafting retries

The existing script request includes an exact host/section output contract derived
from the selected story records. It groups available and required story IDs by
ordered nonempty section, names active hosts and exposes the conversation-only
turn limit. Examples are valid JSON with actual configured speaker names, not
ambiguous speaker placeholders. Newspaper examples enumerate SHIFT, IMPACT and
WATCH separately and state the existing short-copy limits before drafting.
Small evidence sets receive a shorter prose guide, not a full-edition filler target.

The model checks these constraints within its existing response; this is not an
additional model call or independent factual verification. Structural validators,
coverage checks and original-source factual approval remain authoritative. Only
draft instructions change: no automatic evidence deletion, quota increase or
verification bypass. Prompt text grows slightly; runtime/retry improvements must
be measured in ordinary runs and are not guaranteed by passing local tests.

## Adaptive newspaper reading

The 4.1.11 console defaults to READABLE when the freshly checked owner record
contains a supported reading projection. The runner adds that bounded copy only
for a published ready paper whose final JSON matches the verified manifest.
Only editorial fields survive; internal source IDs, evidence, local paths and
URLs do not. Text, lists and exact highlights become semantic DOM nodes, never
HTML. Visual facts retain labels, values, details and captions in text; original
charts and design remain available under PAGES and in the unchanged PDF.

Old/invalid/legacy projections use page previews without historical regeneration.
New metadata lists exact same-edition preview URLs; a stable owner-update revision
refreshes corrected editions without random cache busting on every selection.
Partial preview failures preserve the pages that loaded. Selection uses the
existing fresh owner-server check, stale callbacks are invalidated on logout or
view changes, and private reading copy is not persisted in the offline shell.
There is no additional model call, cloud read/subscription or provider; existing
metadata writes/transfer/storage are slightly larger. Real iOS/PWA and VoiceOver
testing, and adoption on normally generated papers, remain open.

## Adoption requirements and limitations

The local application currently targets Windows. The unattended runner targets
GitHub-hosted Ubuntu. Each operator needs their own Google AI Pro access,
read-only Gmail OAuth client, and explicitly labeled newsletters. Firebase,
GitHub Actions, and Cloudflare are optional and are required only for the web,
private-feed, or unattended features.

The project does not provide a shared hosted multi-user service, bypass
publisher access controls, read attachments, use login cookies to fetch
articles, or guarantee that third-party free-tier limits will never change. It
also does not make the private RSS feed cryptographically user-authenticated.
Operators must review provider plans, quotas, permissions, and terms before
deployment.

For adoption, follow [README.md](../README.md), then the detailed guides in
[SETUP.md](SETUP.md), [PUBLISHING.md](PUBLISHING.md),
[CLOUD_RUNNER_SETUP.md](CLOUD_RUNNER_SETUP.md), and
[CLOUD_CLOCK_SETUP.md](CLOUD_CLOCK_SETUP.md).
