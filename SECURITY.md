# Security policy

## Supported version

Security fixes are made on the default branch. Deployments are independently
operated, so operators are responsible for updating their private copy and
rotating their own credentials.

## Report a vulnerability privately

Do not open a public issue containing a credential, private feed URL, account
identifier, newsletter content, or exploit details. Use this repository's
**Security** tab to open a private vulnerability report.

If private reporting is unavailable, open a public issue containing only a
request for a private contact channel. Do not include the vulnerability itself.

## If a secret may have leaked

1. Disable schedules and the private runner.
2. Revoke the affected grant or token at its provider.
3. Remove it from the repository and its complete Git history.
4. Create a replacement with the narrowest possible scope and expiry.
5. Review GitHub Actions logs, artifacts, Firebase releases, and Cloudflare logs.
6. Run `scripts/audit-public-readiness.py --history` before restoring service.

Deleting a file or repository is not a substitute for revoking a credential.

## Deployment rules

- Keep every operational deployment repository private.
- Never place Gmail, Antigravity, Firebase, GitHub, or Cloudflare credentials in
  source, issues, screenshots, chat, build artifacts, or generated Hosting files.
- Keep Firebase on Spark with Cloud Billing unlinked and GitHub Actions spending
  at zero.
- Give repository write access only to trusted operators. A writer can change a
  workflow so that it uses encrypted secrets.
- Treat the private RSS URL as a password and rotate its path after exposure.
- Use separate OAuth clients for Gmail read-only access and Firebase owner access.
