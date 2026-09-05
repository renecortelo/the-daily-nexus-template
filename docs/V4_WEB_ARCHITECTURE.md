# V4 private web and cloud runner

## Deployment model

V4 is a single-owner Progressive Web App (PWA), a timing-only Cloudflare Free
alarm clock, and an unattended Linux runner on GitHub Actions. Every user
deploys a separate private copy with a separate
Firebase project, Google OAuth clients, encrypted repository secrets, private
feed path, and GitHub repository. There is no shared multi-user backend.

The browser can:

- sign in with Google and prove that its Firebase UID is the private owner;
- queue a dated generation;
- create, edit, pause, and delete parameterized schedules;
- view minimal runner status;
- play published private episodes and open the matching newspaper;
- install the responsive interface on iPhone from Safari.

After owner authorization, the browser receives schedule/generation settings,
status records, references, timed transcripts, and private media URLs so it can
operate the console. It never receives raw Gmail bodies, article bodies,
Antigravity request files, Gmail/Antigravity/Firebase deployment credentials,
local runtime paths, or detailed runner logs.

## Authentication and authorization

The PWA uses Firebase Google Authentication with session-only persistence. It
signs out after 15 minutes without activity and exposes an explicit Sign Out
control. Authentication is not authorization: Firestore private access requires
an owner document whose ID exactly matches the signed-in Firebase UID.

The cloud runner uses four independent revocable grants:

- a `gmail.readonly` OAuth refresh token;
- an Antigravity personal session copied from the operating-system keyring;
- a Firebase owner refresh token for owner-scoped Firestore;
- a Firebase CLI refresh token for static Hosting and rule deployment.

They persist as encrypted GitHub Actions secrets until the operator rotates or
deletes them. A workflow step writes temporary owner-only copies. Later steps
receive file paths, not secret environment variables. Cleanup removes the
copies, and GitHub destroys the ephemeral Linux machine after the job. The
workflow uploads no Actions artifact, audio, or source bundle, but the
GitHub-hosted machine processes selected source text and generated output during
the run, and GitHub retains workflow logs according to repository settings.

Repository collaborators with write access can modify workflows and can
therefore cause encrypted secrets to be used. Give write access only to a fully
trusted operator. Sharing the software means creating a separate deployment,
not adding another person to the production repository.

## Scheduling and zero-cost design

The dedicated Cloudflare Free Durable Object uses one alarm for the next due
schedule. It stores only opaque schedule IDs, enabled state, IANA timezone,
start time, weekday numbers, and a dispatched local date. It sends a
`workflow_dispatch` request to the existing private workflow only when work is
due. The Worker holds a narrow, expiring GitHub token with Actions write access
to one private repository; it never receives Gmail, Antigravity, Firebase
deployment credentials, newsletter content, the private feed path, or the full
schedule parameters.

The PWA writes a timing-only `clockSchedules` projection atomically with a
full schedule. Firestore rules require its timing to match the authoritative
schedule. The Worker uses the browser's short-lived Firebase ID token only to
ask owner-locked Firestore for that projection. Application code does not store
the token. The Worker environment separately retains the scoped GitHub dispatch
token as a Cloudflare secret until it is rotated or deleted.

The workflow receives an opaque ID and the original local occurrence date, then
reloads the full owner-only schedule. A late alarm therefore cannot start an
unrelated queue request or change the intended date after midnight. The existing
execution claim remains the duplicate-generation guard for at-least-once alarm
delivery.

The Cloudflare clock is the sole automatic trigger. GitHub schedule polling is
not configured, avoiding delayed schedule events and idle Actions minutes. A
concurrency lock allows only one runner, and the full job has a 60-minute
timeout. A generic manual batch safely dispatches one follow-up batch when
queued work remains after its two-episode safety limit.

The runner creates an immutable owner-scoped Firestore claim for each
schedule/date or manual request before generation. This prevents a new
ephemeral machine from repeating the same task. A failed claim is not
automatically retried; queue a new manual request after correcting the cause.

V4 deliberately does not use Firebase Functions, Cloud Scheduler, Cloud Run,
App Hosting, Storage, Google Cloud service accounts, or paid model API keys.
The Firebase project must remain on Spark with Cloud Billing unlinked.
Antigravity must keep both of these values:

```json
{
  "useG1Credits": false,
  "enableTelemetry": false
}
```

GitHub Free private repositories have a finite Actions allowance. Set the
account's Actions budget to zero with its usage stop enabled, and monitor included-minute usage. The
workflow refuses production generation if the repository is public, if the
runner is not Linux, or if the trigger is not `schedule` or
`workflow_dispatch`.

## Firestore data

All operational documents live below `/users/{ownerUid}`:

- `schedules`: timing and validated generation parameters;
- `clockSchedules`: timing-only projection used by the Cloudflare alarm;
- `runRequests`: manual queue state;
- `executions`: immutable schedule/date claims and final state;
- `episodes`: date, title, duration, status, private published URLs, bounded
  source counts, up to 100 public references, and up to 500 timed transcript
  segments;
- `runner/status`: a short state and timestamp.

Firestore does not store raw Gmail bodies, article bodies, the structured script
file, newspaper body copy, Gmail message IDs, OAuth credentials, Antigravity
request files, local paths, or detailed local exceptions. The timed transcript
does contain the spoken narration and must be treated as private editorial data.

## Existing-feed preservation

Firebase Hosting deployments are complete static releases. The publisher
validates the existing private RSS feed and asks Firebase to clone retained
MP3/PDF content hashes into the new release on the server side. The runner
therefore downloads only the small RSS inventory, not the prior media archive.
URLs must remain on the exact configured Firebase host and private path. File
counts, MIME types, individual sizes, and total bytes are bounded. If
preservation cannot be verified, deployment stops rather than replacing the
live archive.

## Per-user setup

1. Create a dedicated Firebase project on Spark and leave Cloud Billing
   unlinked.
2. Enable Google Authentication, create Firestore Standard edition, and deploy
   the tracked Hosting and Firestore configuration.
3. Sign in once, copy the resulting Firebase UID, and create
   `/owners/<UID>` manually in Firestore.
4. Configure and test Gmail read-only access, Antigravity, private publishing,
   and the web runner locally.
5. Keep the deployment repository private.
6. Add the encrypted GitHub runner secrets listed in
   [CLOUD_RUNNER_SETUP.md](CLOUD_RUNNER_SETUP.md), then complete the separate
   timing-only [CLOUD_CLOCK_SETUP.md](CLOUD_CLOCK_SETUP.md).
7. Run `Private cloud runner` manually once. Confirm an idle result before
   enabling a schedule in the PWA.
8. Create a disabled schedule, verify the clock synchronization, queue one
   manual date, verify Apple and READ, then enable the morning schedule.

The Firebase project ID, API key, owner UID, feed path, refresh tokens, email
address, OAuth JSON, runtime database, logs, and generated episodes must never
be committed.

## iPhone installation

Open the Firebase Hosting URL in Safari, sign in, tap **Share**, then
**Add to Home Screen**. This creates a PWA icon without an Apple Developer fee.
Each new browser session still requires Google sign-in.

## Incident response

Disable the GitHub workflow and affected schedules first. Then rotate the
relevant GitHub secret, revoke the associated Google/Firebase grant, review
Firebase Authentication users and repository collaborators, and rotate the
private feed path if its URL may have leaked. Never make a deployment repository
public; publish a new clean-history template repository instead.
