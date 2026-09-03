# V3: private Apple Podcasts publishing at zero additional cost

## What V3 publishes

The Daily Nexus uses a dedicated Firebase Hosting project on the no-billing
**Spark** plan. It publishes only static files:

- the private RSS feed;
- Apple-compatible MP3 episodes;
- the show artwork;
- the matching PDF editions.

Apple Podcasts follows that RSS URL directly. The feed includes
`<itunes:block>yes</itunes:block>` and is not submitted to Apple's public
catalog.

The URL has this shape:

```text
https://PROJECT_ID.web.app/p/RANDOM_128_BIT_SECRET/feed.xml
```

This is **private by possession**, not private by account. It is unlisted and
hard to guess, but anyone who obtains the complete URL can access it. Treat the
URL like a password. V3 keeps it out of ordinary run output and log files.

Spotify Premium does not host or import an arbitrary private RSS feed. Apple
Podcasts is therefore the zero-additional-cost private route for this V3.

## One-time setup

### Step 1 — create a dedicated Firebase project

This step requires you:

1. Open [Firebase Console](https://console.firebase.google.com/).
2. Sign in with the personal Google account you want to use for The Daily Nexus.
3. Choose **Create a project**.
4. Name it something recognizable, such as `The Daily Nexus Private`.
5. Note the exact **Project ID**. It may differ from the friendly project name.
6. Disable Google Analytics when offered; this app does not need it.
7. Finish creating the project.
8. Confirm the plan shown in Firebase is **Spark**.
9. Do not select **Upgrade**, do not select **Blaze**, and do not attach a Cloud
   Billing account.

Use a project dedicated to this podcast. Do not reuse a work or unrelated
Google Cloud project.

### Step 2 — prepare static Hosting

This step also requires you:

1. In the new Firebase project, open **Build > Hosting**.
2. Choose **Get started**.
3. Stop when Firebase displays its local command instructions. The Daily Nexus
   already contains a static-only `firebase.json` and performs the deployment.
4. Do not enable Functions, App Hosting, Cloud Run, or Storage. Firestore is
   unnecessary for feed-only use, but the optional V4 web console requires the
   Standard `(default)` Firestore database and the tracked owner-only rules.

### Step 3 — save the Firebase Project ID locally

In **The Daily Nexus > GEN > Apple Private Feed**:

1. Choose **Configure**.
2. Enter the exact Firebase Project ID from Step 1.
3. The app stores the project host and creates a cryptographically random
   128-bit feed path in the operating system credential vault. The ignored
   local `config.toml` stores only the non-secret `keyring` storage marker.

The secret is not printed by setup, written to TOML, or placed in Git. It is used only as the
unguessable path in the hosted URL. Publishing remains disabled after this
step.

Command-line equivalent:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  -m audiodigest --config config.toml configure-publishing `
  --project-id YOUR_PROJECT_ID
```

### Step 4 — authenticate the local Firebase tool

In the same Apple Private Feed panel:

1. Choose **Firebase Sign-in**.
2. A temporary PowerShell window opens because this authorization is
   interactive.
3. Complete Google sign-in with the account that owns the dedicated Firebase
   project.
4. If Firebase asks about Gemini features or usage reporting, answer **No**.
5. Return to The Daily Nexus after the window reports success.

This authorization can deploy only with the permissions of that signed-in
Google account. It does not enable publishing in The Daily Nexus. The helper
sets and rechecks the Firebase CLI's optional Gemini and usage-reporting
preferences as `false`.

Command-line equivalent:

```powershell
.\scripts\authenticate-firebase.ps1
```

### Step 5 — explicitly confirm the zero-cost boundary

Return to Firebase Console and check the project once more:

- the plan says **Spark**;
- no Cloud Billing account is linked;
- the project has not been upgraded to Blaze.

Then, in the app, choose **Confirm Spark + Enable** and accept only if all three
statements are true. The confirmation is recorded locally and bound to this
exact Firebase Project ID.

The app also rejects:

- paid or metered AI credential environment variables;
- a Firebase configuration containing paid backend services;
- a base URL that is not the configured project's standard Firebase host;
- a missing or mismatched Spark confirmation;
- Antigravity AI-credit fallback or telemetry.

## First publication and Apple connection

### Step 6 — perform one manual publication

Keep the publishing preference set to **Manual** for the first test:

1. In **PLAY**, listen to a completed episode.
2. In **READ**, inspect its PDF edition.
3. Return to **GEN** and select that episode's date.
4. Choose **Publish selected date**.
5. Wait for the app to report success.

Before it marks the episode published, V3:

1. validates the cover dimensions and color mode;
2. validates every hosted MP3 codec, sample rate, channels, and bitrate;
3. validates the private RSS structure and unique episode identifiers;
4. deploys only the static Hosting tree;
5. downloads the live RSS feed from Firebase;
6. confirms the selected episode is present;
7. checks that its live audio enclosure is reachable with the correct media
   type.

If deployment or live verification fails, the local episode remains intact and
is not marked published.

### Step 7 — copy the private Apple URL

After the first successful publication:

1. In **GEN > Apple Private Feed**, choose **Copy Apple URL**.
2. Do not paste it into messages, screenshots, GitHub issues, or public notes.

The URL is intentionally hidden from routine process logs. The explicit
command-line equivalent is:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  -m audiodigest --config config.toml private-feed-url
```

### Step 8 — follow it in Apple Podcasts

On iPhone or iPad:

1. Open **Podcasts**.
2. Open **Library**.
3. Open the **More** menu (`...`).
4. Choose **Follow a Show by URL**.
5. Paste the complete private RSS URL.
6. Choose **Follow**.
7. Open The Daily Nexus and play the first episode.

Following a private URL can sync across Apple devices signed in to the same
Apple Account. Because the show is not in Apple's public catalog, public search,
ratings, reviews, and Apple catalog analytics are not expected.

Apple's current references:

- [How Apple Podcasts distributes shows](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners)
- [Test a podcast RSS feed](https://podcasters.apple.com/support/828-test-your-podcast)
- [Apple audio requirements](https://podcasters.apple.com/support/893-audio-requirements)

## Same-run automatic publishing

Enable this only after the first manual publication succeeds and the episode
plays in Apple Podcasts:

1. Open **GEN > Episode Preferences**.
2. Set publishing to **Automatic after a successful run**.
3. Choose **Save + Verify**.
4. Turn off **Local-only test** for any run that should publish.
5. Start the generation normally.

The eight-stage run then collects, writes, verifies, renders the PDF, renders
the MP3, commits the local edition, and finally publishes and remotely verifies
the private Apple feed. A failure in an earlier stage prevents the upload.

Automatic mode does not schedule the app, run in the background, or wake the
computer. It only adds publishing to a generation run you start.

To stop all uploads immediately:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  -m audiodigest --config config.toml disable-publishing
```

## Free-tier guardrails

Firebase Hosting currently includes 10 GB of storage and 10 GB per month of
data transfer at no cost. V3 adds a more conservative local 1 GB hosted-tree
limit. On Spark, the project has no billing configuration; Firebase can stop
serving after a quota is exhausted instead of billing for overage.

Never solve a quota warning by linking billing or upgrading to Blaze. Retain
fewer hosted episodes instead.

- [Firebase Hosting quotas and pricing](https://firebase.google.com/docs/hosting/usage-quotas-pricing)

## If the private URL leaks

1. Disable publishing.
2. Change the secret path locally.
3. Republish the retained episodes.
4. Follow the new URL in Apple Podcasts.
5. Stop using the old URL.

This static private-by-link design is appropriate for one person or a small
trusted pilot. A future public multi-user product would need separately
revocable feeds or an authenticated backend and a separate security design.
