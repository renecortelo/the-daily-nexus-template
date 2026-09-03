#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_OS:-}" != "Linux" ]]; then
  echo "The private cloud wrapper may run only on a Linux GitHub Actions runner." >&2
  exit 1
fi

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
runtime_root="$RUNNER_TEMP/the-daily-nexus"
credential_path="$runtime_root/secrets/antigravity-keyring.json"
export XDG_DATA_HOME="$runtime_root/keyring-data"
export XDG_RUNTIME_DIR="$runtime_root/keyring-runtime"
mkdir -p "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"

if [[ ! -f "$credential_path" || -L "$credential_path" ]]; then
  echo "The temporary Antigravity keyring credential is unavailable." >&2
  exit 1
fi

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  exec dbus-run-session -- bash "$0"
fi

export PATH="$HOME/.local/tdn-tools:$PATH"

cleanup_keyring() {
  set +e
  secret-tool clear service gemini username antigravity >/dev/null 2>&1
  rm -f "$credential_path"
}
trap cleanup_keyring EXIT HUP INT TERM

# The disposable-password keyring exists only inside this single ephemeral runner.
# GitHub destroys the machine after the job, and the final workflow cleanup
# removes its XDG data directory even when generation fails.
keyring_environment="$({
  printf '%s' 'daily-nexus-ephemeral-runner'
} | gnome-keyring-daemon --unlock --components=secrets)"
keyring_environment+=$'\n'
keyring_environment+="$(gnome-keyring-daemon --start --components=secrets)"
while IFS= read -r assignment; do
  assignment="${assignment#export }"
  assignment="${assignment%;}"
  case "$assignment" in
    GNOME_KEYRING_CONTROL=* | SSH_AUTH_SOCK=*) export "$assignment" ;;
  esac
done <<< "$keyring_environment"

secret-tool store \
  --label="Antigravity CLI session" \
  service gemini \
  username antigravity \
  < "$credential_path"
rm -f "$credential_path"

if ! secret-tool lookup service gemini username antigravity >/dev/null; then
  echo "The temporary Antigravity keyring session could not be verified." >&2
  exit 1
fi

# Two typical 20–30 minute episodes can fit inside the protected one-hour
# workflow limit. A conservative elapsed-time guard leaves a clean margin for
# final publishing and cleanup; remaining work stays queued for the next poll.
batch_limit=2
batch_budget_seconds=$((25 * 60))
batch_started=$SECONDS
completed_tasks=0

# The Cloudflare alarm carries one opaque schedule occurrence.  Keep that
# dispatch isolated: a delayed alarm must not consume an unrelated manual
# request, and a manual batch must not accidentally inherit the clock inputs.
if [[ -n "${TDN_SCHEDULE_ID:-}" ]]; then
  runner_args=(python -m audiodigest --config config.toml.cloud web-runner --schedule-id "$TDN_SCHEDULE_ID")
  if [[ -n "${TDN_SCHEDULE_DATE:-}" ]]; then
    runner_args+=(--schedule-date "$TDN_SCHEDULE_DATE")
  fi
  result="$("${runner_args[@]}")"
  printf '%s\n' "$result"
  exit 0
fi

if [[ -n "${TDN_SCHEDULE_DATE:-}" ]]; then
  echo "TDN_SCHEDULE_DATE requires TDN_SCHEDULE_ID." >&2
  exit 1
fi

while (( completed_tasks < batch_limit )); do
  if (( completed_tasks > 0 && SECONDS - batch_started >= batch_budget_seconds )); then
    echo "Private batch time budget reached; remaining queue work will continue on the next cloud check."
    break
  fi
  result="$(python -m audiodigest --config config.toml.cloud web-runner)"
  printf '%s\n' "$result"
  if grep -q '"status": "idle"' <<< "$result"; then
    break
  fi
  if grep -q '"status": "already-claimed"' <<< "$result"; then
    break
  fi
  ((completed_tasks += 1))
done
