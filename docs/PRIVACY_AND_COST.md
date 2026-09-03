# Privacy and cost boundary

## Data flow

1. Gmail supplies the connected account email for display and explicitly labeled newsletter
   bodies through `gmail.readonly`.
2. The selected episode date—not an email address or Gmail content—is sent to Wikimedia to
   retrieve On This Day and date-specific Current Events evidence.
3. Public article URLs are fetched locally without login cookies. Known newsletter tracking
   wrappers are never opened: locally decodable destinations are converted to direct public
   URLs, while opaque trackers and utility pages are ignored.
4. Selected newsletter/article/research text is placed briefly in an isolated local request file and
   processed through the Antigravity CLI using the existing Google AI Pro account.
5. The final host dialogue is synthesized locally with Kokoro.
6. The matching two-page digest and both previews are rendered locally with
   ReportLab and PyMuPDF. The current renderer does not download external images;
   it uses only the bundled TDN mark.
7. When publishing is enabled, only the final MP3, 2-3 page PDF, RSS metadata,
   bundled cover, and public source links go to Firebase Hosting.
8. V4 stores only owner-scoped schedule parameters, queue state, minimal runner
   state, and published episode metadata in Firestore. A separate
   `clockSchedules` collection contains timing only (opaque schedule ID,
   enabled state, timezone, time, and weekdays) for the private Cloudflare
   alarm. Raw newsletters, scripts, credentials, local paths, and detailed
   errors remain outside Firebase.

Personal email and attachments are rejected by configuration. Do not reuse the source label for
confidential correspondence.

Gmail refresh tokens and the cached connected-account email are stored in Windows Credential
Manager, not project files. The desktop launcher can sign in or disconnect. Disconnecting first
requests revocation at Google's fixed OAuth endpoint and then removes both local values even if
the network request cannot be confirmed.

## Antigravity isolation

- Google OAuth is required; a Google Cloud project, API key, or Vertex credential is forbidden.
- `useG1Credits` must be explicitly `false`, so paid AI-credit fallback cannot occur.
- `enableTelemetry` must be explicitly `false`.
- Both values are checked before every Antigravity call.
- Calls run with `--sandbox` in `%LOCALAPPDATA%\AudioDigest\antigravity-workspace`.
- The selected `audio-digest` custom agent exposes only the read-only `view_file` tool.
- Headless permission is limited to `read_file` for that isolated workspace only.
- Non-interactive model input lives in one randomly named `request-*.json` file, which is
  removed immediately after the subprocess exits, including on failure.
- Quota, authentication, permission, billing, or credit errors stop publication.

Antigravity still has to send the supplied text to Google's model service to generate the
editorial output. The CLI may retain local conversation records in its own user-profile state.
Disabling telemetry prevents optional product telemetry; it does not turn model inference into
an offline operation.

## Static-feed privacy

Firebase Spark serves static files. The 128-bit path is an unlisted capability URL, not account
authentication. Apple Podcasts and anybody who learns the URL can fetch it. This is appropriate
only for the selected-newsletter pilot.

The feed sets `itunes:block=yes` and `X-Robots-Tag: noindex, nofollow, noarchive`. These are
directory and crawler controls, not access control.

The V4 web console itself is owner-authenticated, but it links to the same
capability URLs after sign-in. Its service worker caches only the public
application shell and explicitly ignores the private `/p/` tree. A browser or
Apple Podcasts client can still buffer episode media during playback.

The web session uses session-only Firebase persistence plus a 15-minute idle
sign-out. The unattended Linux runner uses separate encrypted GitHub Actions
secrets for Gmail, Antigravity, Firebase owner access, and Firebase deployment.
Secrets are materialized only into owner-only temporary files on an ephemeral
machine and can be revoked independently. Antigravity's session is inserted
into an ephemeral Secret Service keyring only while the generation command is
running, then cleared. The separate Cloudflare Free clock has only an expiring,
single-repository GitHub Actions-dispatch token and timing-only schedule data;
it does not receive any of those generation secrets. Repository write access is
therefore restricted to the trusted operator.

## Charge prevention

Startup aborts if it sees:

- `OPENAI_API_KEY` or `CODEX_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- Vertex or service-account environment credentials
- Antigravity `useG1Credits` missing or not `false`
- Firebase Functions, Cloud Run, App Hosting, or Storage configuration
- Missing local confirmation that the Firebase project remains on Spark

V4 uses only static Hosting, Authentication, and Firestore's free quota. The
morning scheduler is a timing-only Cloudflare Free Durable Object that wakes a
private Linux GitHub Actions workflow only for due work; no Firebase Function
or Cloud Scheduler job is created. The workflow has no public-repository
production path and uploads no private artifacts. No timer-based GitHub poll is
configured, so idle Actions minutes are not consumed. Generation credentials do
not exist on the runner while dependencies are being installed.
Publishing clones the existing Hosting version server-side, so the ephemeral
runner reads only the RSS inventory instead of consuming Spark transfer by
redownloading the retained audio archive. Keep Firebase's live-channel release
history limited to three previous releases so obsolete rollback content is
deleted.

Firebase Spark requires no payment method and stops service when its free Hosting quota is
exhausted. Never link Cloud Billing or upgrade the project to Blaze.

The existing subscription's included Antigravity quota is used. Google AI
credits and Google Developer Program cloud credits are deliberately unused.
Kokoro model files may be downloaded from Hugging Face on first use without an
account token; later synthesis uses the local cache. The app disables optional
Hugging Face telemetry.
