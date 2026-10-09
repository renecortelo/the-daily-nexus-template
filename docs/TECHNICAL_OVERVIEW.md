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
The closing quote and attribution stay verbatim. Its separate original comment
targets 12–20 words (25 maximum); a deterministic guard keeps only complete short
copy or the quotation alone, without another model call or whole-script retry.
Reviewed Spanish/Catalan public-news names have synthesis-only phoneme overrides
in `speech.py`, shared by desktop and cloud. Written names remain unchanged;
local validated overrides take precedence. These are English-voice approximations,
not native-language voice switching or automatic transliteration of unknown names.

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
