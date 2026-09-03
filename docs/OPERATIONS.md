# Operations

## Normal behavior

- Nothing runs until the user opens **The Daily Nexus** and presses a run button.
- Opening the launcher may request the connected account profile to display its email address.
  It does not read messages until an episode run starts.
- The previous local calendar day is selected.
- Only messages with the exact configured Gmail label are read.
- A failed run leaves the existing feed unchanged.
- A failed retry does not replace the last completed local edition for that date.
- A published date is immutable and cannot be published twice.
- No episode is produced when there is no substantive source material.
- Scripts target 2,850–3,800 words, or roughly 20–30 minutes at the current local
  voices. A short first draft is automatically expanded once using underdeveloped
  verified stories already collected in Stage 2. If all required stories are covered
  and the evidence is exhausted, a comprehensive shorter script proceeds without
  filler instead of failing on an arbitrary minimum.
- Once a script is verified, recoverable work is copied to
  `%LOCALAPPDATA%\AudioDigest\episodes\YYYY-MM-DD\in-progress`. If Windows sleeps
  or the app closes during audio rendering, the script and any completed PDF remain there.

Logs are written to `%LOCALAPPDATA%\AudioDigest\logs`. They contain status and errors, not
the source payload. Temporary source JSON is removed at the end of each run.

## Sign in or sign out of Gmail

Use the **Google account** card in the launcher. **Sign in with Google** opens Google's normal
browser consent flow and requests only `gmail.readonly`. **Disconnect account** asks Google to
revoke the grant, then deletes the local token and cached account identity from Windows
Credential Manager.

If remote revocation cannot be confirmed because the computer is offline, the local token is
still removed and the launcher reports the warning. Remove AudioDigest manually from the
Google Account connections page if complete remote revocation is required immediately.

## GEN, PLAY, and READ

- **GEN** contains account controls, source label, one- or two-host settings,
  publication mode, run controls, live stage, elapsed time, and estimated
  remaining time. **Refresh status** re-reads the current process and library.
- **PLAY** lists completed local episodes and provides icon controls, pitch-preserving
  speed, volume, seek controls, clickable references, a synchronized transcript, and an
  animated signal while audio is playing. It opens the local MP3 directly; listening
  does not require the internet.
- **READ** lists the same editions and renders both digest pages inside the app
  with page and zoom controls. The full PDF can be opened in the system viewer.
- **ABOUT** explains the workflow and cost/privacy boundaries without showing account
  names, Gmail labels, or message data.
  Reading a completed local edition does not require the internet.

## Change the label, hosts, voices, or tones

Use the preferences panel in **GEN**. The label must match Gmail exactly,
including capitalization and nested `/` levels. When signed in, saving checks
that the label exists before changing the local configuration.

Choose Dalia or Nox for a one-host briefing, or choose both for a conversation.
Each host has an independent delivery personality and editorial tone;
the local Kokoro engine voice names remain hidden. Dario Novelli remains the
non-speaking agent, editor, and producer. Changing voices does not add a paid
speech service. Tone and host structure are passed to Antigravity's
script-writing and verification stages.

## Add or remove a newsletter

- Add: create a Gmail filter for its exact sender and apply `AudioDigest/Source`.
- Remove: remove or disable that filter and remove the label from future messages.
- Avoid whole-domain filters unless the publisher genuinely changes sender addresses.

## Run or rerun

Open **The Daily Nexus** from the desktop. **Yesterday** updates the calendar
selection; press **Run** to create the selected date.

For command-line troubleshooting:

```powershell
.\scripts\run-daily.ps1 -EpisodeDate YYYY-MM-DD
```

Published dates are intentionally immutable. Do not delete database rows
casually. If an older local edition used the retired audio-script fallback, rebuild
only its READ edition from the saved verified stories:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  -m audiodigest --config config.toml rebuild-newspaper --date YYYY-MM-DD
```

This runs the independent newspaper writer and quality reviewer, then replaces only
the local newspaper JSON, PDF, and page previews. It does not rerender audio or reuse
the spoken script. `render-newspaper` is reserved for rerendering an already valid
independent newspaper after layout-only changes.

## Manual and automatic publication

Manual mode is recommended. Generate locally, review in PLAY and READ, then use
**Publish selected** in GEN.

Automatic mode publishes only after a run you started completes every stage
successfully. It does not create a schedule or background service. Full setup and
private Apple Podcasts steps are in [PUBLISHING.md](PUBLISHING.md).

## Rotate the feed URL

1. Pause every schedule and private runner.
2. Recheck that the Firebase project is on Spark with no linked billing account.
3. Rotate without printing the new bearer path:

   ```powershell
   & "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
     -m audiodigest --config config.toml configure-publishing `
     --project-id YOUR_PROJECT_ID --rotate-secret
   ```

4. Record the Spark confirmation again, enable publishing, and run one manual
   publication. Use the app's explicit copy action only when adding the new URL
   to Apple Podcasts.
5. Remove the old Firebase release only after confirming the replacement feed.

If `secret_storage = "keyring"` but its credential-vault entry is missing, the
app stops instead of silently changing the feed URL.

## Stop or pause

Press **Stop** in the launcher to end an active run. Closing the launcher when it is idle
fully stops AudioDigest. There is no background service or scheduled task to disable.

## Uninstall

1. Delete the desktop app named **The Daily Nexus**.
2. Remove the Apple Podcasts feed.
3. Delete the Firebase Hosting site or project in Firebase Console if desired.
4. Remove the Gmail OAuth grant from Google Account security.
5. Delete the `AudioDigest`, `TheDailyNexusFirebase`, and web-runner credentials
   from Windows Credential Manager.
6. Remove `%LOCALAPPDATA%\AudioDigest` only after preserving wanted audio.
