# Separate browser and automation authorization

This is an explicit, per-deployment migration, not a generation step. Do not run
it in a public template repository, during generation, or from a scheduled job.
It does not rotate Gmail, Antigravity, deployment credentials, or private feeds.

## What changes

- The browser still signs in with Google, using the authorized owner account.
- A dedicated Firebase Anonymous identity authenticates **only the runner**. It
  is not a guest login: anonymous identities without an exact owner grant have
  no application access. The website does not offer anonymous sign-in.
- The owner grant binds that identity to one owner's data. The runner can read
  schedules and queued requests and update execution/status/publication records.
  It cannot edit schedules, owner records, clock projections, or its own grant.
- An active (or revoked) grant makes Firestore reject owner browser tokens one
  hour after their original `auth_time`, including refreshed tokens. Cloud Clock
  browser requests use the same owner-document check; stored alarms do not need
  a browser token and continue running.

The 15-minute idle lock remains a browser control. Logout clears the local console,
but does not immediately revoke copied Firebase tokens. Static Apple RSS/media
URLs are still capability links, not account-authenticated downloads; this
migration does not invalidate copies or rotate a working feed.

## Safe activation order

1. Deploy the reviewed `firestore.rules` **before** pushing the new private runner
   code: older rules do not authorize the new grant-document check. Then update
   the private runner code.
   **Before activation**, absent or `prepared` authorization retains legacy access
   for compatibility; deploying the rules alone does not close the session gate.
2. In the dedicated Firebase project's Authentication → Sign-in method, enable
   **Anonymous**, alongside Google. Do not enable billing or upgrade to Identity
   Platform. If automatic anonymous-account cleanup is configured, disable it for
   the long-lived runner identity. This uses Firebase Auth, not a new paid service.
3. Wait until no private cloud runner is queued or running. From the private
   repository with its local configuration, credential vault, authenticated GitHub
   CLI and Python environment, run:

   ```powershell
   $env:PYTHONPATH = 'src'
   & "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" scripts/migrate-runner-identity.py
   ```

4. Complete the Google owner approval in the opened browser. The script creates
   a dedicated identity, secures its refresh token locally, records a `prepared`
   grant and makes a bounded schedule read. It sends only that token, over stdin,
   to the existing encrypted `TDN_FIREBASE_REFRESH_TOKEN` secret of the private
   repository. It checks runner activity again, then activates server expiry.
   It never generates an episode or prints credentials.
5. Sign in again to the web console and inspect the next normal scheduled run.
   Do not queue a full test episode just to validate authorization.

## Failures and revocation

If migration fails before activation, check its prerequisites and run the same
script again. A `prepared` grant can resume only with its exact locally secured
identity. A failure after secret replacement leaves that runner usable through
the prepared grant, but browser expiry is **not yet enforced**. Do not blindly
delete the grant or copy another user's credentials. Active/revoked grants cannot
be downgraded to prepared through client rules; the script refuses automatic
rotation once activated.

To revoke automation, set the authorization document's `mode` to `revoked` using
the trusted Firebase console, and remove the corresponding private GitHub secret.
Revocation does not restore the old owner-token bypass. Re-provisioning/rotation
requires a separately reviewed operator action. Deleting an active grant through
an administrator would restore legacy compatibility, so do not delete it.

Provider administration uses privileged access and can bypass Firestore rules.
Protect Firebase/GitHub accounts with MFA and restrict repository collaborators.
The migration does not remove historical secrets or revoke existing provider
grants, and does not promise zero risk.

## Tests

Python tests simulate provisioning/secret-install failures and wrong identities.
The local Firestore emulator suite additionally exercises actual rules with
synthetic owner/runner accounts. It refuses production projects or remote hosts:

```powershell
$env:FIRESTORE_EMULATOR_HOST = '127.0.0.1:8088'
node --test tests/firestore-rules.cjs
```

Start the official Firestore emulator with project `demo-nexus-security` and the
repository's `firestore.rules` first. No mailbox, deployed database, or feed is
used. Emulator checks are separate from the usual browser/Python test commands.
