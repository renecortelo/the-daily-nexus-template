# Private cloud runner setup

This is a per-user deployment. Keep the production repository private and give
write access only to a fully trusted operator. A person with write access can
change a workflow so that encrypted secrets are used.

## Cost controls first

1. Confirm the Firebase project still says **Spark** and has no linked Cloud
   Billing account.
2. In GitHub **Billing > Budgets and alerts**, create an Actions budget at
   **$0** (or the lowest available value), enable **Stop usage when budget limit
   is reached**, and turn on included-usage alerts.
3. Do not add a payment method solely for this project.
4. Do not enable Firebase Functions, Cloud Run, App Hosting, Storage, or Cloud
   Scheduler.
5. Keep `useG1Credits=false` and `enableTelemetry=false`.
6. In **Firebase -> Hosting -> Release history -> Release storage settings**,
   keep only three previous releases. This prevents obsolete rollback versions
   from accumulating against the Spark storage allowance.

The private workflow has a 60-minute timeout. Its lightweight probe receives
only the Firebase queue credential, then deletes it before any dependency is
installed. When work is due, Gmail, Antigravity, and publishing credentials are
materialized only after installation and are removed again on success or
failure. The dedicated Cloudflare Free clock dispatches this workflow only when
a schedule is due or GEN needs a wake-up; complete
[CLOUD_CLOCK_SETUP.md](CLOUD_CLOCK_SETUP.md) after this runner setup. The
dedicated Cloudflare clock is the sole automatic trigger after its proof run.

The publisher fetches only the small private RSS inventory. It clones the
current Hosting version on Firebase's side, adds the new MP3/PDF and application
shell, and retires media that has left the configured retention window. Older
MP3/PDF files are reused by their existing Hosting content hashes; they are not
downloaded to GitHub and uploaded again each morning.

## One-time encrypted-secret transfer

Complete local Gmail, Antigravity, Firebase publishing, and web-runner
authentication first. Install GitHub CLI from its official installer, then
authenticate it to the private deployment repository:

```powershell
gh auth login
gh auth status
```

Run the project helper:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  .\scripts\configure-private-cloud-secrets.py
```

The helper reads the existing local grants and configuration, verifies that the
repository is private, reduces Gmail authorization to the fields needed for
refresh, reads Antigravity's active `gemini:antigravity` session from Windows
Credential Manager, and sends these values to GitHub through standard input:

- `TDN_GMAIL_TOKEN_JSON`
- `TDN_FIREBASE_REFRESH_TOKEN`
- `TDN_ANTIGRAVITY_KEYRING_JSON`
- `TDN_FIREBASE_DEPLOY_TOKEN`
- `TDN_FIREBASE_PROJECT_ID`
- `TDN_FIREBASE_API_KEY`
- `TDN_FIREBASE_OWNER_UID`
- `TDN_FIREBASE_SECRET_PATH`
- `TDN_SPARK_CONFIRMED`

It prints secret names only. It never prints values, writes an export bundle, or
commits a credential. During a due job, the Antigravity session exists only in
an ephemeral Linux Secret Service keyring and is cleared after the run. GitHub
encrypts repository secrets and does not expose them to pull requests from
forks.

## Deploy the new execution rules

The cloud runner uses owner-scoped Firestore execution claims. Deploy the
tracked rules from the private project before enabling schedules:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\node-tools\node_modules\.bin\firebase.cmd" `
  deploy --only firestore:rules --project YOUR_FIREBASE_PROJECT_ID
```

Use the real project ID only in the command or ignored local configuration;
never add it to documentation or tracked files. Deploy the web shell through
the incremental private release process in
[CLOUD_CLOCK_SETUP.md](CLOUD_CLOCK_SETUP.md), so an existing private media
archive and the exact cloud-clock Content Security Policy are preserved.

## Private proof run

1. Push the V4 branch.
2. In GitHub, open **Actions -> Private cloud runner**.
3. Choose **Run workflow**.
4. With no due task, confirm that it reports `No private cloud task is due` and
   skips the model/runtime installation.
5. In the PWA, queue one already-tested date.
6. Run the workflow manually again.
7. Confirm generation, remote feed verification, playback in Apple Podcasts,
   and the READ edition.
8. Only then enable an automatic schedule.

For a 06:00 ready target, a start time around 03:00 leaves room for the
normal generation duration. The Cloudflare alarm dispatches the tested
default-branch workflow after it is configured; no GitHub cron is used.

## Rotation and removal

Disable the workflow and schedules before rotating credentials. Replace only
the affected GitHub secret, revoke the old Google/Firebase grant, then perform
another idle proof run. Deleting the repository removes its encrypted secrets,
but it does not revoke the corresponding grants at Google or Firebase.

Never turn the deployment repository public. Create a separate clean-history
template repository for sharing; templates do not inherit encrypted Actions
secrets.
