# Review runtime without generating another episode

Use measurements from normal completed runs before changing generation. The
offline reviewer requires no extra service, dependency, billing plan, provider
permission or model call. It does not change schedules, restart failures,
publish anything or relax the 60-minute job cap.

## Run the reviewer

Activate the project's Python environment as described in [SETUP.md](SETUP.md).
If GitHub CLI is already authenticated to your **private deployment**, stream
one existing run's logs directly into the reviewer:

```powershell
gh run view RUN_ID --log | python -m audiodigest.runtime_report
```

Replace `RUN_ID` locally with your run number. Do not commit a personal example,
raw log or private report. No GitHub integration is needed in the reviewer:
the command on the left supplies input; the reviewer itself never uses a token
or accesses the network. Always check that the GitHub command succeeded. An
empty review exits with code 2, not a successful zero-duration measurement.

For an existing local log containing `TDN_PROGRESS` events:

```powershell
python -m audiodigest.runtime_report --input existing-run.log
python -m audiodigest.runtime_report --input existing-run.log --format json
```

Keep the input local and ignored. Streaming is preferable when no log file is
needed. Only allowlisted terminal events are accepted, with a maximum of 200
retained samples. Repeated finish timestamps are deduplicated. Arbitrary log
text, task names, account identities, URLs, credential fields, error messages
and individual timestamps are not included in the aggregate output. This is
an allowlisted metric reader, **not a general-purpose log redaction utility**.

## Interpret the output

- **All attempts** includes completed, failed and interrupted generation.
  **Completed only** separates the distribution of delivered work.
- Stage totals identify where measured time went. Each row states its sample
  count: a missing stage is unknown, not a measured zero. Optional paper work
  and varying newsletter loads make the content mix important.
- Model, speech, encoding and rendering operations are **nested in stages**.
  Do not add their times to stage totals or treat them as independent savings.
- Retry counts and fixed terminal codes help locate instability; they do not
  isolate retry duration or establish which factual claim was rejected.
  Historical codes reflect the diagnostic implementation used at that time.
- `speech_reused_blocks` counts exact voice/language/text matches reused within
  one temporary episode render. It is not a count of removed transcript turns
  or a duration saving. Older runs without this counter remain unknown.
- Median and nearest-rank p90 describe the supplied samples, not a guaranteed
  duration or an account-wide forecast. A small sample's p90 may be its maximum.
- Logs normally contain generation timings, not the enriched owner document's
  ready-by/setup/resource measurements. Missing target measurements remain
  unknown. A killed job without a finish event is absent from this report;
  inspect its last heartbeat separately.
- Observed job time is cumulative within a queue batch and excludes final
  cleanup. It is not summed. Account-wide billed minutes, remaining allowance,
  Hosting storage and download transfer remain unknown; use provider consoles.

## Make an optimization decision

1. Review recent normal runs, including failures. Do not create extra episodes
   merely to populate a benchmark.
2. Choose the dominant stage or operation, not the easiest-looking dependency.
3. Compare equivalent content, newspaper setting, voice configuration and output
   length. Aggregate logs deliberately contain no personal schedule identifiers;
   keep any content-mix comparison in private working notes.
4. Measure before and after and verify source coverage, factual gates, script
   quality, pronunciation and final audio. Synthetic speed tests alone do not
   prove an equivalent production result.
5. Preserve bounded repairs, terminal failures, the protected shutdown deadline,
   account isolation and no-paid-fallback operation. Do not trade away fact
   checking, silently truncate content or increase runtime caps for a speed gain.

This tool is opt-in and local. It is not installed as a polling job, added to
runner generation, or used to dispatch/requeue work automatically.

## Response and speech profiling

Normal generation now records fixed numeric request/response UTF-8 byte totals,
model-response time accepted or rejected by structural validation (milliseconds),
accepted script patch/full-draft response sizes, and accepted patch edit counts.
Accepted JSON is not factual approval. These timings include CLI/tool overhead;
failed exits and process timeouts are not included in the accepted/rejected split,
but remain in the model operation total. Bytes are neither tokens nor billed usage.
No rejected prose, edit anchors, model response, private URL or source ID is saved.
Exact-context sharing also records packet input/sent entry counts and repeated
context characters not resent. Counts accumulate over validated packets, not distinct
newsletters; support for every fact is unchanged. They are not tokens or saved seconds.

Speech preparation, Kokoro's phonemization/inference pipeline, PCM validation/write,
and edge-silence inspection have separate timings. Pipeline/write are nested in
the speech operation; preparation/edges belong to the audio stage. Never add nested
timings to their parent totals. Sample counts describe unique synthesized chunks,
not all replayed chunks; voices, speed, text, pauses and encoding are unchanged.
New numeric metrics ride existing events/heartbeats and the bounded final profile;
no new polling or per-metric cloud write is added. Missing legacy metrics are unknown.

For an optional **local synthetic** profile, use an environment with the existing
audio dependencies and public model/voice/language files already cached:

```powershell
python scripts/profile-speech-runtime.py --timeout 120 --threads 2
```

This disposable CPU subprocess uses only built-in fictional text, disables network
connections and Hub downloads, excludes application/provider secret environment
variables, captures library output, and deletes temporary synthetic WAVs normally.
The parent enforces a 30–300 second wall-time limit including model initialization
and warm-ups. Missing caches fail; nothing is installed or downloaded automatically.
Only fixed phases and numeric samples are emitted; incomplete profiles exit with
code 2 rather than claiming success. It is not a workflow or normal-run extra step.
No production thread setting changes. A Windows result cannot establish Linux-runner
savings, and matching repeated PCM does not certify subjective naturalness.

Before adopting a synthesis change, compare equivalent text, voices, pronunciation,
sample counts and complete encoded output on equivalent hardware. The current
one-thread and inference-mode experiments do not justify a production change.

## Optional newspaper admission and deadline

Only protected cloud runs use this policy; local desktop behavior is unchanged.
Before starting paper generation, reserve `max(900, ceil(0.55 * verified_script_words)
+ 180)` seconds for speech, encoding, publication and promotion. This conservative
policy scales beyond the old 15-minute floor; it is not a measured completion guarantee.
The remaining optional paper window is capped at ten minutes. With less than three
minutes available, no paper model/render call starts. Existing structural and full
original-source review remain required; rejected or incomplete copy is never published.

The shared deadline covers writing, review/repairs, JSON/PDF/previews and progress-file
promotion. Subprocess timeouts use that earlier deadline; the existing owned Unix
alarm is shortened in the main thread and restored minus elapsed time afterwards,
never renewed from scratch. Cooperative checks remain on other platforms/threads.
Python signals do not guarantee interruption of long native C work; the unchanged
55-minute protected task deadline and 60-minute workflow cap remain the final guards.
See [Python signal behavior](https://docs.python.org/3.12/library/signal.html).

An insufficient/exhausted optional window records `deferred`, not a disabled user
preference or factual approval. The verified podcast continues with no paper and no
automatic paper retry. Fixed numeric reserve/window/outcome counters reach the owner
monitor. Cost/security errors and actual global exhaustion still abort the task.
Ordinary future runs must verify adoption; no extra test generation is needed.

## CLI results versus editorial validation

CLI wrapper/exit/provider failures are distinct from model JSON and editorial
constraints. Fixed failure categories, layer, permitted HTTP/exit numbers and byte
counts identify the boundary without storing raw stdout/stderr, messages, unknown
statuses, accounts or newsletter text. Attempt timings/history survive recovery.
Explicit authentication/access/quota/request failures and denied tools fail fast;
payment/credit requests remain fatal no-spend guards. Unknown errors are not given
an invented quota or authentication cause.

A recoverable CLI failure can use only the existing remaining attempt. When a
protected deadline exists, at least sixty seconds must remain. Local runs keep
their existing operation timeout. It resends the unchanged current request,
never a fictional editorial repair or provider error text. Provider and editorial
failures share the same attempt limit; there is no separate retry loop or episode
requeue. Partial/unsuccessful results cannot pass as valid model content. Validated
draft corrections still use the existing source-grounded editorial repair path.
Invalid optional usage telemetry is discarded rather than crashing an otherwise
valid response; all body/JSON/source validators still run unchanged. No model,
voice, credential, permission or account fallback is introduced. These controls
reduce avoidable failures/waste, not guarantee availability of an external provider.
