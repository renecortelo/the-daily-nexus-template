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
