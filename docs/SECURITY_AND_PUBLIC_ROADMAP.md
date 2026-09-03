# Security and public roadmap

## What is safe today

The current application is a personal Windows desktop app:

- Gmail access is read-only and restricted to messages carrying the configured label.
- The Gmail refresh token and cached connected-account email are stored in Windows Credential
  Manager.
- **Sign in with Google** opens Google's system-browser OAuth flow and requests only
  `gmail.readonly`.
- **Disconnect account** revokes the saved Google grant and deletes the local authorization
  and account identity.
- Antigravity uses a separate Google OAuth session and refuses G1 credit fallback.
- Newsletter content and credentials are never committed to Git.
- Wikimedia receives only the selected episode date and the app's user-agent.

The private deployment repository must not be made public because its earlier
history can retain deployment locators and personal commit metadata even after
the current files are sanitized. A public release must be a new repository
created from the audited identifier-free tree with fresh history. Encrypted
Actions secrets are not copied into a repository created from a template.

## Gmail constraint for a public app

Reading message bodies requires `gmail.readonly`, which Google classifies as a restricted
scope. A ready-to-use production application for arbitrary Google users must complete Google's
OAuth verification, publish a homepage and privacy policy on a verified domain, justify the
minimum scope, and may require an annual third-party security assessment.

During development, an External OAuth app in Testing mode can use an explicit test-user
allowlist. That is suitable for a small pilot, not general distribution. See Google's current
[OAuth production-readiness overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
and [verification requirements](https://support.google.com/cloud/answer/13464321).

Zero-additional-cost public distribution is therefore realistic in either of these forms:

1. Open-source self-deployment where each advanced user supplies a private
   repository, Google OAuth clients, Antigravity subscription login, and
   Firebase Spark project.
2. A small private test pilot whose users are explicitly listed in the developer's OAuth
   consent-screen test audience.

A turnkey public service operated under one OAuth project should be treated as a later,
funded security project.

## Private Apple Podcasts path

Apple Podcasts is a podcast reader and directory; it does not host an ordinary private RSS
feed for this app. The feed and MP3 files must first be hosted at an HTTPS address.

The existing Firebase Hosting publisher already builds the correct shape:

```text
https://<project>.web.app/p/<128-bit-secret>/feed.xml
https://<project>.web.app/p/<128-bit-secret>/audio/<episode>.mp3
```

For a personal zero-additional-cost pilot:

1. Create a dedicated Firebase project on the Spark plan with no billing account.
2. Configure only static Firebase Hosting.
3. Generate a private feed secret and record the Spark-plan confirmation.
4. Run several local-only episodes and inspect their audio and show notes.
5. Review the edition in PLAY and READ, then use manual publishing for the first
   episode.
6. In Apple Podcasts, choose **Library → More → Follow a Show by URL**, then paste the private
   feed URL.

Apple explicitly supports private RSS URLs and recommends `itunes:block` to keep them out of
the public directory. AudioDigest already emits `itunes:block=yes`. See Apple's
[private RSS guidance](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners).

The secret URL is bearer access: anyone who obtains it can listen or open its
two-page PDF. A multi-user release should use a separate revocable feed URL per
person. That requires an authenticated backend or specialized podcast host and
is outside the current static, zero-additional-cost boundary.
