# Dedicated private cloud clock

This optional V4 component replaces timing-critical GitHub `schedule` events
with a tiny Cloudflare Durable Object alarm. It is a **clock and dispatcher**,
not a generator:

```text
PWA schedule
  -> Firestore timing-only projection
  -> Cloudflare Free alarm
  -> GitHub private workflow_dispatch
  -> existing Linux runner
  -> Gmail / Antigravity / local audio / Firebase publishing
```

It lets a due schedule wake the runner at its selected time without keeping a
Windows machine on. The existing GitHub workflow remains the only place that
can access Gmail, Antigravity, the Firebase publishing token, the private feed
path, newsletter text, or editorial settings.

## What the clock can and cannot see

The clock retains only this safe timing projection for each schedule:

```json
{
  "scheduleId": "opaque-id",
  "enabled": true,
  "timezone": "UTC",
  "startTime": "04:45",
  "weekdays": [0, 1, 2, 3, 4]
}
```

It does **not** receive a Gmail label, email address, newsletter content,
article URL, run name, host setting, script, transcript, feed path, Firebase
refresh token, Gmail token, Antigravity session, Firebase deployment token, or
private episode URL. Firestore rules require this projection to match the
corresponding full private schedule exactly.

The browser uses a short-lived Firebase ID token and exact-origin CORS to ask
the clock to synchronize or wake the runner. The Worker verifies the token by
using it against owner-locked Firestore; it does not store the token.

## Cost guardrails

Use only a **Cloudflare Free** account. Do not add a payment method or enable
Workers AI, R2, KV, D1, Queues, Browser Rendering, Analytics Engine, Logpush,
or any paid add-on. One SQLite-backed Durable Object and a few alarms per day
stay far below the Free allowance. If a free limit is reached, the clock stops
instead of silently charging the project.

Keep Firebase on Spark with Cloud Billing unlinked, and configure a zero-cost
GitHub Actions budget with its usage stop enabled. GitHub is invoked only when a schedule is due or a
manual GEN request needs a wake-up. It has no periodic polling trigger.

## One-time setup

Do this after the cloud-clock source is pushed to the private repository.

1. Create or sign in to a Cloudflare account on the Free plan. Confirm that no
   payment method or paid add-on is enabled.
2. In GitHub, create a **fine-grained personal access token** with:

   - Resource owner: your account.
   - Repository access: **Only select repositories**, then select only this
     private deployment repository.
   - Repository permission: **Actions: Read and write**.
   - Expiration: 90 days or less.

   This token can dispatch the existing workflow; it cannot read Gmail or
   Firebase credentials. Do not use a broad classic token.
3. From PowerShell in this project, sign in and deploy the Worker:

   ```powershell
   Set-Location .\cloud-clock
   npx wrangler@4 login
   npx wrangler@4 deploy
   ```

   Wrangler prints a URL shaped like
   `https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev`. Treat it as a
   deployment-specific value: it is public by design, but do not commit it.
4. Set these Worker secrets. Each command prompts privately; do not paste a
   value into a chat, source file, or commit:

   ```powershell
   npx wrangler@4 secret put TDN_GITHUB_DISPATCH_TOKEN
   npx wrangler@4 secret put TDN_GITHUB_REPOSITORY
   npx wrangler@4 secret put TDN_FIREBASE_PROJECT_ID
   npx wrangler@4 secret put TDN_OWNER_UID
   npx wrangler@4 secret put TDN_ALLOWED_ORIGIN
   ```

   Use the following values only at the private prompt:

   | Secret | Value |
   | --- | --- |
   | `TDN_GITHUB_DISPATCH_TOKEN` | the restricted fine-grained token from step 2 |
   | `TDN_GITHUB_REPOSITORY` | `YOUR_PRIVATE_REPOSITORY_OWNER/YOUR_PRIVATE_REPOSITORY_NAME` |
   | `TDN_FIREBASE_PROJECT_ID` | the existing private Firebase project ID |
   | `TDN_OWNER_UID` | the Firebase Authentication UID already authorized in `/owners` |
   | `TDN_ALLOWED_ORIGIN` | the exact `https://YOUR_FIREBASE_PROJECT.web.app` PWA origin |

   `TDN_GITHUB_WORKFLOW` and `TDN_GITHUB_REF` are safely defaulted to
   `private-cloud-runner.yml` and `main`; leave them unset unless the private
   deployment deliberately uses different values.
5. In GitHub repository **Settings -> Secrets and variables -> Actions**, add
   `TDN_CLOUD_CLOCK_URL` with the Worker URL from step 3. This is kept as a
   secret solely to prevent a deployment-specific identifier from entering the
   repository. It is never a Gmail, Antigravity, or Firebase credential.
6. Deploy the updated Firestore rules and web shell using the existing private
   Firebase credentials. From the project root:

   ```powershell
   & "$env:LOCALAPPDATA\AudioDigest\node-tools\node_modules\.bin\firebase.cmd" `
     deploy --only firestore:rules --project YOUR_FIREBASE_PROJECT_ID

   .\scripts\deploy-private-web-console.ps1 `
     -ProjectId YOUR_FIREBASE_PROJECT_ID `
     -CloudClockUrl "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev"
   ```

   The helper reads the existing Firebase CLI authorization locally, stages
   only the web shell, and does not print the token. The incremental publisher
   copies the endpoint and adds only its exact origin to the Hosting
   `connect-src` policy. Future episode publishing preserves that configuration
   through the encrypted GitHub `TDN_CLOUD_CLOCK_URL` secret.
7. Sign in to the PWA. In **SCHED**, the chip should change from
   `CLOUD CLOCK // SETUP REQUIRED` to a next-alarm state. Save a disabled
   schedule, verify that it synchronizes, then enable it.

## Proof run

1. Set a test schedule a few minutes in the future.
2. Confirm that the PWA reports the clock's next alarm.
3. Verify one `Private cloud runner` GitHub workflow begins with the opaque
   schedule ID and local date inputs.
4. Confirm the generated episode, PDF, and private feed are correct.
5. Queue one GEN request and confirm it dispatches the runner immediately.
6. Confirm no idle GitHub Actions workflow is created between those explicit
   clock events. Keep `workflow_dispatch` for the Cloud Clock and manual
   recovery.

The runner can process two typical full episodes within its one-hour safety
window. If more manual queue work remains, it securely dispatches its next
batch after cleanup; it does not depend on a timer-based poll.

An alarm is at-least-once. A rare duplicate dispatch is safe because the
existing Firestore schedule/date execution claim prevents a duplicate episode.
If an alarm is late after midnight, it passes the original local schedule date
to the runner so the intended episode date is retained.

## Rotation and incident response

If the GitHub dispatch token or Worker deployment may have been exposed:

1. Pause schedules and disable the Worker route or GitHub workflow.
2. Revoke the fine-grained GitHub token.
3. Create a new restricted token and replace only
   `TDN_GITHUB_DISPATCH_TOKEN` through Wrangler.
4. Re-deploy the Worker and update `TDN_CLOUD_CLOCK_URL` if its URL changed.
5. Run a disabled-schedule proof before re-enabling automation.

Do not rotate Gmail, Antigravity, or Firebase publishing credentials unless
they were independently exposed: they are never sent to this clock.
