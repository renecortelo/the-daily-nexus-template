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

task_failed=false
record_task_failure_output() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'task_failed=%s\n' "$task_failed" >> "$GITHUB_OUTPUT"
  fi
}

trap 'record_task_failure_output; cleanup_keyring' EXIT
trap 'exit 130' HUP INT TERM

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

# A single 20–30 minute episode can occasionally take longer during article
# retrieval or local audio rendering. Process one task per protected Actions
# run, then let the credential-free continuation dispatch the next queued
# task. This avoids a second episode inheriting too little of the one-hour
# job limit and being cancelled halfway through.
batch_limit=1
batch_budget_seconds=$((45 * 60))
batch_started=$SECONDS
completed_tasks=0
generation_status_file=""

cleanup_generation_status() {
  if [[ -n "$generation_status_file" ]]; then
    rm -f "$generation_status_file"
  fi
}
trap 'record_task_failure_output; cleanup_generation_status; cleanup_keyring' EXIT

run_generation_task() {
  local status
  local filter_status
  local -a pipeline_status
  generation_status_file="$(mktemp "$RUNNER_TEMP/tdn-result.XXXXXX")"
  set +e
  # The filter forwards only the numeric timing protocol in real time. It
  # discards raw output instead of retaining newsletter text or credentials.
  PYTHONUNBUFFERED=1 "$@" 2>&1 | python -u -m audiodigest.progress \
    --result-file "$generation_status_file"
  pipeline_status=("${PIPESTATUS[@]}")
  status=${pipeline_status[0]}
  filter_status=${pipeline_status[1]}
  set -e
  generation_result="$(< "$generation_status_file")"
  cleanup_generation_status
  generation_status_file=""
  if (( filter_status != 0 )); then
    echo "The protected timing stream could not complete." >&2
    return "$filter_status"
  fi
  printf 'Protected task result: %s\n' "$generation_result"
  if (( status == 20 )); then
    task_failed=true
    return 0
  fi
  return "$status"
}

# The Cloudflare alarm carries one opaque schedule occurrence.  Keep that
# dispatch isolated: a delayed alarm must not consume an unrelated manual
# request, and a manual batch must not accidentally inherit the clock inputs.
if [[ -n "${TDN_SCHEDULE_ID:-}" ]]; then
  runner_args=(python -m audiodigest --config config.toml.cloud web-runner --schedule-id "$TDN_SCHEDULE_ID")
  if [[ -n "${TDN_SCHEDULE_DATE:-}" ]]; then
    runner_args+=(--schedule-date "$TDN_SCHEDULE_DATE")
  fi
  run_generation_task "${runner_args[@]}"
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
  run_generation_task python -m audiodigest --config config.toml.cloud web-runner
  result="$generation_result"
  if grep -q '"status": "idle"' <<< "$result"; then
    break
  fi
  if grep -q '"status": "already-claimed"' <<< "$result"; then
    break
  fi
  ((completed_tasks += 1))
done
