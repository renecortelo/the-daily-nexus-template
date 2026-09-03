# Public release checklist

This repository is maintained separately from every operational deployment.
Never merge, rebase, or cherry-pick a private deployment's commits into the
public history: commit metadata and deleted blobs can retain personal data.

## Prepare a candidate

1. Keep the working deployment private and clean.
2. Transfer code changes as a reviewed patch or tracked-file export. Do not copy
   `.git`, ignored files, generated Hosting output, media, configuration, or
   credentials.
3. Review every added filename and every changed line. Replace deployment IDs,
   hosts, account details, local paths, emails, regional preferences, and private
   operational examples with neutral placeholders.
4. Confirm that all credential paths point outside the clone or to ignored files.
5. Do not paste secret values into command arguments, logs, issues, or pull
   requests.

## Verify before committing

Run from the public candidate with the private config path supplied only to the
local comparison process:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  .\scripts\audit-public-readiness.py `
  --local-config "ABSOLUTE_PATH_TO_PRIVATE_CONFIG.toml"

gitleaks dir . --redact --no-banner
.\scripts\test.ps1
```

The comparison reports only categories and file locations; it does not print
private values.

## Create a release commit

Use a neutral project identity and disable personal commit signing:

```powershell
git config user.name "Dario Novelli"
git config user.email "noreply@users.noreply.github.com"
git config commit.gpgsign false
```

After committing, run both full-history checks:

```powershell
& "$env:LOCALAPPDATA\AudioDigest\venv\Scripts\python.exe" `
  .\scripts\audit-public-readiness.py --history --strict-metadata `
  --local-config "ABSOLUTE_PATH_TO_PRIVATE_CONFIG.toml"

gitleaks git . --redact --no-banner
```

## Publish safely

1. Push the candidate to a new **private** repository first.
2. Confirm it has only the intended clean history and neutral commit identity.
3. Confirm that it has no repository secrets, environment secrets, Actions
   artifacts, releases, deployments, private URLs, or unexpected workflow logs.
4. Manually dispatch the public-readiness workflow and let every check pass.
   Automatic private pushes and pull requests skip this job, so only this
   explicit pre-publication check uses included private Actions time.
5. Change visibility to public, then immediately enable secret scanning, push
   protection, and private vulnerability reporting.
6. Manually dispatch public readiness once more after the visibility change;
   this creates and verifies the public status-check context.
7. Mark the repository as a template only after the public check succeeds.

If any scan fails, keep the remote private. Revoke any real credential that may
have entered Git; deleting the file or rewriting history alone is insufficient.
