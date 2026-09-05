# Setup

## 1. Install local prerequisites

Install on the Windows machine:

- Git.
- Python 3.11 or newer, with Python 3.12 recommended.
- Node.js 20 or newer.
- uv, the free Python package installer.
- FFmpeg and FFprobe.
- Antigravity CLI.

Cloud-synced folders are optional and should be used only for a separately configured output
backup. Run the application from a local clone rather than a synchronized working folder.

From this local project folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
winget install --exact --id astral-sh.uv
.\scripts\setup-windows.ps1 -InstallFfmpeg
```

The setup creates the Python environment and local Firebase CLI under
`%LOCALAPPDATA%\AudioDigest`, then creates a native desktop app named **The Daily Nexus**. It does not
configure a paid API, scheduled task, or background service.

## 2. Prepare Gmail's explicit allowlist

1. In Gmail, create the default label `AudioDigest/Source`, or choose your own nested label.
2. For each approved newsletter, create a Gmail filter using its exact From address and apply
   `AudioDigest/Source`.
3. Do not apply the label to personal, transactional, or confidential mail.
4. Search the label in Gmail and inspect the results before continuing.

Gmail may classify newsletters as Promotions; that does not matter. The explicit label is the
source allowlist.

## 3. Create read-only Gmail OAuth credentials

1. Open Google Cloud Console and create a project dedicated to AudioDigest.
2. Do **not** attach Cloud Billing.
3. Enable only the Gmail API.
4. Open **Google Auth platform > Branding** and configure:
   - App name: `AudioDigest`
   - Your own email as the support and contact email
   - Audience: **External** for a personal Gmail account
5. Under **Data Access**, add only
   `https://www.googleapis.com/auth/gmail.readonly`.
6. Under **Audience**, publish the app to **In production**. A personal-use app owned by the
   signing-in user does not need verification, although Google may display an unverified-app
   warning. External apps left in Testing issue refresh tokens that expire after seven days.
7. Open **Google Auth platform > Clients**, create a **Desktop app** client, and download its
   JSON file.
8. Rename it to `client_secret.json` and place it at
   `%LOCALAPPDATA%\AudioDigest\secrets\client_secret.json`.
9. Keep it private; do not place it in this project, Google Drive, or source control.

The application requests only `gmail.readonly`. The OAuth file stays in local Windows storage,
and the refresh token is stored through Windows Credential Manager.

## 4. Authenticate Antigravity and Gmail

Run:

```powershell
.\scripts\authenticate.ps1
```

For Antigravity:

1. Choose **Google OAuth**.
2. Do **not** choose **Use a Google Cloud project**.
3. Sign in with the personal Google account that owns Google AI Pro.
4. Complete the browser authorization and onboarding.
5. At the Antigravity prompt, enter `/quit` to return to the script.

The script writes and re-checks these global Antigravity settings before continuing:

```json
{
  "useG1Credits": false,
  "enableTelemetry": false
}
```

AudioDigest also checks both values before every model call. It runs Antigravity with
`--sandbox` in a dedicated workspace and selects the bundled `audio-digest` custom agent,
whose only tool is `view_file`. The headless allow-rule grants `read_file` only for that
isolated workspace.

The script then opens Gmail's consent flow. Select the intended Gmail account and grant the
single read-only scope. If you are not using the default label, first open the launcher while
signed out, enter the exact label under **HOST + EPISODE CONTROLS > GMAIL SOURCE LABEL**,
and choose **SAVE + VERIFY**. If Google shows
**Google hasn't verified this app**, choose
**Advanced**, continue to AudioDigest, review the scope, and allow it. The script succeeds only
after it finds the configured label.

After setup, the desktop launcher shows the connected Gmail address. Use
**Sign in with Google** to authorize an account. Use **Disconnect account** to revoke the
Google grant and remove its refresh token and cached account identity from Windows Credential
Manager. Gmail authorization and Antigravity subscription authentication are intentionally
separate.

The **GEN** preferences panel controls:

- The exact Gmail source label. When signed in, saving verifies that the label exists before
  changing the configuration.
- One host (Dalia or Nox) or two hosts (Dalia and Nox).
- One of three descriptive delivery personalities for each active presenter.
  The underlying Kokoro voices are constrained to female voices for Dalia and
  male voices for Nox, and technical model names remain hidden.
- One of five editorial tones for each host: neutral, dry wit, fun, warm, or very formal.
- Manual or automatic publishing. Manual is the safe default.

## 5. Optional: prepare V3 private Apple publishing

Publishing is disabled by default. Local generation, playback, and reading do not
need Firebase.

The desktop app now guides the local parts of setup from **GEN > Apple Private
Feed**. Follow [PUBLISHING.md](PUBLISHING.md) for the exact no-billing Spark
project setup, Firebase sign-in, first verified manual publication, private
Apple Podcasts connection, and later same-run automatic mode. Do not enable
publishing until the Firebase Console shows **Spark** and no billing account is
linked.

## 6. Run the safety doctor

Open **The Daily Nexus** and press **DOCTOR** in the SYSTEM panel. For command-line
troubleshooting:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\audiodigest.exe" `
  --config config.toml doctor
```

Resolve every failed check. A missing Spark confirmation is expected until optional publishing
is configured.

## 7. Perform a three-day local dry run

Leave `publish_enabled = false`.

Open **The Daily Nexus** from the desktop. Keep
**Keep this run local (overrides automatic publishing)** selected, choose the
date directly or press **YESTERDAY**, and then press **RUN**.

The launcher may refresh the connected account's email address, but it does not read newsletter
messages until an episode run is started. It does not install a background service, poll Gmail,
schedule a task, or wake the computer.

Command-line equivalents:

```powershell
.\scripts\run-daily.ps1
.\scripts\run-daily.ps1 -EpisodeDate 2026-07-25
```

Inspect output under `%LOCALAPPDATA%\AudioDigest\episodes\YYYY-MM-DD`. Each
completed edition contains the MP3, verified structured script, manifest,
timed transcript, two-page PDF, and both screen previews. During generation, verified
artifacts appear under the date's `in-progress` folder so an interrupted audio render
does not hide work that already finished.

## 8. Optional: enable the private feed

Use [PUBLISHING.md](PUBLISHING.md). Begin with manual publication of a reviewed
edition. Anyone with the private URL can access the static unlisted feed, so
use the credential-vault rotation procedure in
[OPERATIONS.md](OPERATIONS.md) and redeploy if it is exposed.

## 9. Recreate the launcher if needed

```powershell
.\scripts\create-desktop-shortcut.ps1
```

You can also open it from PowerShell:

```powershell
.\scripts\open-launcher.ps1
```
