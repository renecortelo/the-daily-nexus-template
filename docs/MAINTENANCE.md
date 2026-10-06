# Maintaining the free, private toolchain

The runtime is pinned in `uv.lock`, `package-lock.json` and
`cloud-clock/package-lock.json`. These files contain public package names,
download URLs and integrity hashes, not deployment settings or credentials.

- Python: 3.12 in the Linux runner; Windows supports 3.11–3.12. A uv-managed
  Python 3.12.15 is available for current Windows security fixes.
- Antigravity CLI: 1.3.0, with verified SHA-256 downloads for Windows and Linux.
- Firebase CLI: 15.32.1; browser SDK: 12.19.0.
- Wrangler: 4.147.0; Node: 24 LTS.
- Kokoro/Misaki remain 0.9.4; Torch remains 2.13.0 CPU on Linux/Windows;
  Transformers remains 5.14.1; ReportLab remains on the tested 4.x branch.

## Safe upgrades

1. Work on a branch. Review upstream release notes and security advisories.
2. Update only the intended packages with `uv lock --upgrade-package PACKAGE`.
   Synchronize with `uv sync --frozen --extra audio --extra dev` and run tests.
3. For CLI tools, update the exact version in the appropriate `package.json`,
   regenerate its npm lock, then use `npm ci`. Never use `npm audit fix --force`.
   Keep the Firebase version guard in `scripts/firebase-clone-deploy.cjs` and
   its tests aligned; the incremental publisher uses internal CLI interfaces.
4. Audit with `python scripts/audit-dependencies.py` and `npm audit` in both
   npm projects. OSV receives only public Python package/version pairs, including
   audio dependencies and alternative Python/platform resolutions. An unavailable
   audit service fails the check rather than returning a false pass.
5. Run the source/privacy audit and independent full-history secret scan before
   copying changes to a public template. npm can include a third-party author's
   email in non-functional deprecation metadata: remove that contact text from
   the lock, without changing package URLs, versions or integrity hashes.
6. Review and merge Dependabot proposals manually. Weekly update proposals do
   not auto-merge, deploy or invoke the private generation workflow.

## Firebase transitive overrides

The locked CLI overrides `basic-ftp`, `uuid`, `@opentelemetry/core` and `chokidar`
to remove known advisories still present in the upstream CLI dependency tree.
The supported path is Hosting publication and Firestore rule deployment; test
these interfaces whenever updating Firebase. Chokidar 4 no longer expands glob
patterns. This application does not use Firebase's emulator file-watch commands;
review their behavior separately before adding emulator support.

Incremental publication must preserve the current Hosting version. If reading or
cloning that version fails, deployment now aborts instead of publishing an empty
replacement. Creating a new version without a clone is allowed only for a site's
first release.

## Antigravity safety after an upgrade

Run `scripts/configure-antigravity.ps1` after installing/upgrading the CLI. It
preserves unrelated settings and explicitly disables credits and telemetry in
the legacy CLI profile and the shared `config/config.json` preferences.
Shared `useAiCredits` can otherwise override the old `useG1Credits` preference.
The application checks both profiles and aborts if either enables credits or
telemetry. The read-only agent does not inherit ambient customizations or MCP
servers. Billing/API-key environments are stripped from model invocations.
CLI diagnostics with account/source details are withheld from process logs;
failed, denied-tool and truncated results are rejected.

Read-only `agy --output-format json -p "/config"` can confirm effective credit
settings without making a model call. Do not publish its complete output; it can
contain local paths or preferences. Authenticate only with personal Google OAuth.

No new paid service, API key, monthly budget policy, generation schedule,
continuation policy or audio/editing algorithm is introduced by this maintenance
update. The private runner's hard limit remains 60 minutes.
