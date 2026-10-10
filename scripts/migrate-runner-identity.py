"""Run explicitly after deploying rules and enabling Anonymous in Firebase Auth."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from audiodigest.config import load_settings
from audiodigest.runner_identity import migrate_runner_identity
from audiodigest.web_runner import WebRunnerError

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    gh = shutil.which('gh')
    if not gh:
        raise WebRunnerError('GitHub CLI authorization is required')

    def github(*args: str, secret: str | None = None) -> str:
        result = subprocess.run(  # noqa: S603 - fixed CLI, never a shell.
            [gh, *args], cwd=ROOT, input=secret, capture_output=True,
            text=True, check=False, timeout=60,
        )
        if result.returncode:
            # Provider output can contain identifiers. Never echo it.
            raise WebRunnerError('private GitHub operation failed; no credentials printed')
        return result.stdout

    repository = json.loads(github('repo', 'view', '--json', 'visibility'))
    if repository.get('visibility') != 'PRIVATE':
        raise WebRunnerError('production authorization is allowed only in a private repository')

    def confirm_idle() -> None:
        for status in ('in_progress', 'queued', 'waiting', 'pending', 'requested'):
            runs = json.loads(github(
                'run', 'list', '--workflow', 'private-cloud-runner.yml',
                '--status', status, '--limit', '1', '--json', 'status',
            ))
            if runs:
                raise WebRunnerError(
                    'runner is not idle; wait for it to finish, then retry migration'
                )

    def install_secret(value: str) -> None:
        github('secret', 'set', 'TDN_FIREBASE_REFRESH_TOKEN', secret=value)

    migrate_runner_identity(load_settings(ROOT / 'config.toml'), install_secret, confirm_idle)
    print('Dedicated runner authorized. Server-side one-hour console expiry is active.')


if __name__ == '__main__':
    try:
        main()
    except (WebRunnerError, OSError, ValueError, subprocess.SubprocessError):
        print(
            'Migration did not finish. No secrets printed. See docs/RUNNER_IDENTITY.md.',
            file=sys.stderr,
        )
        raise SystemExit(1) from None
