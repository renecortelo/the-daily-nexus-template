# Canonical work plan

Updated: 2026-10-17. This is the single backlog, not a release diary.
The agreed order prioritizes speed, safety, privacy and zero-cost operation.
Statuses: pending / in progress / released / awaiting observation / needs user.
Keep IDs stable; every scope, order or status change must have a dated reason below.
Do not silently drop tasks, create new ones from old chronological notes, or claim
production improvement from tests alone. Private deployment/run evidence stays local.

| ID | Priority | Agreed work | Status | Acceptance / next action |
|---|---:|---|---|---|
| T14 | 0 | Derive extraction attribution from validated passage references | Awaiting observation | Application-owned attribution passed synthetic unrelated/unknown/malformed passage regressions in both copies. Exact passages, strict stored evidence and independent factual review remain mandatory. Observe ordinary-run source failures and saved lineage after reviewed release; recovery is not yet established. |
| T13 | 0 | Recover missing generated story identity without another model call | Awaiting observation | Private and public synthetic suites, interface checks and privacy audit pass. Preserve reporting and source lineage; reject malformed identities and unsupported claims. Observe ordinary runs after reviewed release; do not claim production recovery yet. |
| T12 | 0 | Separate CLI/provider failures from editorial validation | Awaiting observation | Implementation passed synthetic recovery, fail-closed and privacy regressions in both code copies. Fixed CLI/model-JSON/editorial diagnostics; no fictional draft repair or extra retry. Verify provider behavior in ordinary runs, without extra test episodes. |
| T15 | 1 | Preserve terminal diagnostics for optional-paper failures | Awaiting observation | Synthetic podcast-success/paper-failure tests preserve the final fixed code/subphase and bounded details without reviewer prose or an earlier recovered cause. Check adoption in ordinary profiles after reviewed release. Whole-paper deadline and factual gates remain mandatory. |
| T01 | 1 | Precise script validation and targeted correction | Released | Fixed rules/locations, existing retry only, no rejected text in logs. Observe future failures. |
| T16 | 2 | Plan first-draft size and shape within existing output budgets | Awaiting observation | Synthetic checks preserve all sections/facts with flexible word guidance and valid paper example IDs/shape. No extra model call, factual deletion, prose clipping or longer episode. Measure ordinary-run rejection rates and time after reviewed release before claiming improvement. |
| T02 | 2 | Smaller model requests without losing evidence | Released | Compact schema, post-selection scores omitted; selected facts and original supports retained. |
| T03 | 3 | Measure patch/context efficiency in actual runs | Awaiting observation | Numeric request/response sizes, retry timing and exact context sharing shipped; equivalent-content ordinary runs must establish savings. |
| T04 | 4 | Profile Kokoro without changing output quality | Awaiting observation | Numeric CPU/affinity/thread/sample-rate context passed privacy/regression checks; synthesis settings are unchanged. Local bounded experiments did not complete a throughput/output comparison. Collect ordinary Linux context and complete an equivalent-output benchmark before any thread tuning. Optimization benefit is unproven. |
| T05 | 5 | Fit optional newspaper within protected execution budget | Awaiting observation | Implementation validated with synthetic regressions; whole paper phase bounded, dynamic completion reserve, clear deferred reason. Verify adoption in ordinary runs, not an extra episode. |
| T06 | 6 | Reconcile execution time and provider consumption | Awaiting observation | Bounded read-only account job accounting passed synthetic checks and a real provider read, including previous attempts, whole-job time and other private workflows. Unknown jobs are unmeasured; remaining balance requires a fresh Billing meter. Planning averages do not change episode cutoffs; reconcile future observations before treating sums as billed consumption. |
| T07 | 7 | Investigate newsletters without extracted news | Pending | Explain observed gaps, especially unknown reasons; do not treat absence of extraction as absence of useful news. |
| T08 | 8 | Perceptual naturalness and name pronunciation | Pending | Compare specific excerpts, isolate script/preparation/voice; technical metrics alone are insufficient. Listener feedback may be needed. |
| T09 | 9 | Review real newspaper readability and visual usefulness | Pending | Semantic non-repetition, complete sentences, useful visuals and readable two-page target. Use existing approved editions. |
| T10 | 10 | Physical mobile/accessibility validation | Needs user | iPhone/PWA, keyboard, VoiceOver and compact player; synthetic browser tests are not device certification. |
| T11 | 11 | Maintainability and remaining privacy procedures | Pending | Further module separation; reviewed feed-link rotation procedure only with explicit operator authorization. Preserve existing controls. |

## Change log

- 2026-10-17: T14/T15/T16 implementation and both-copy synthetic checks passed;
  moved to awaiting ordinary-run observation, not proven speed or recovery.
  T04 adds runtime context but its bounded local benchmark is incomplete, so no
  synthesis tuning was made. T06 adds tested, bounded read-only account accounting;
  measured jobs are not an account balance. Reviewed export/privacy/deployment
  gates remain mandatory. No episode, retry, schedule or spending change was made.
- 2026-10-17: User authorized implementation of T14, T15, T16, T04 and T06
  in this order. Work is in progress; no new episode/retry is authorized. Local
  synthetic profiling and read-only provider/job accounting are allowed; thread
  tuning still requires a completed equivalent-output comparison. All unchanged
  source, time, retry, privacy and no-spend controls remain release requirements.
- 2026-10-17: Read-only incident/runtime review adds three proposed follow-ups:
  T14 for repeated redundant source-lineage disagreements, T15 because optional
  paper fallback clears its terminal cause, and T16 for oversized first drafts
  and avoidable paper-shape retries. They precede lower-priority pending work;
  T01-T13 keep their IDs and no task was removed. No functional implementation,
  schedule change, new generation or retry was authorized by this review.
- 2026-10-17: T04's remaining work is pending: existing instrumentation stays
  released, but variable measured throughput needs numeric runtime context and
  a controlled comparison before choosing thread/inference settings. T06 now
  distinguishes the completed private account review from unimplemented budget
  reconciliation. Production evidence and account figures remain private.
- 2026-10-15: T13 implementation and synthetic checks passed in both copies;
  the public history/source privacy scan found no leaks. Changed status to
  awaiting observation, not verified production benefit. No new episode was run.
- 2026-10-15: Added urgent T13 after an extraction with a missing local story
  ID exhausted the existing retry allowance. T13
  extends the existing duplicate-ID normalization to absent/blank IDs only;
  facts, citations, source checks and the retry/time limits stay unchanged.
  T12's CLI diagnosis and retry were observed, but the whole generation did not
  recover; T12 therefore remains awaiting observation. T01-T12 retain their IDs,
  scope and priority. No further test episode is authorized.
- 2026-10-15: User authorized urgent T12 after an incident exposed an earlier CLI
  adapter path not covered by field-level editorial diagnostics. It temporarily
  precedes T06; T01-T11 retain their IDs, relative priorities and scope. No tasks
  removed. No extra episode, provider call, retry allowance or longer workflow is
  authorized. Production recovery cannot be guaranteed by diagnostic tests alone.
- 2026-10-15: T12 implementation passed complete Python/web regressions in both
  code copies and public source/history checks. Moved to awaiting observation:
  implementation coverage is established, provider recovery in ordinary runs is
  not. Optional usage telemetry cannot invalidate otherwise valid model content;
  body/source verification and no-spend controls remain mandatory. Deployment and
  review evidence stay local. T06 remains the next independent implementation block.
- 2026-10-14: Established this ledger from the eleven-item speed-prioritized plan.
  No new tasks added and none removed. T01/T02 are shipped; T03/T04 are explicitly
  awaiting real observations rather than falsely marked fully finished.
- 2026-10-14: User authorized T05 implementation without episode generation/retry.
  Its scope includes a paper-phase cap/admission rule and fixed diagnostic outcomes,
  not longer runner limits, fewer factual checks or paid compute.
- 2026-10-14: T05 implementation passed synthetic optional-phase and publication
  tests and public-template checks. Moved to awaiting observation: neither measured
  production savings nor universal completion within the reserve are established.
  Source/deployment release evidence is recorded locally; other task priorities and
  scopes are unchanged. T06 remains the next independent implementation block.

## Release checks for every task

Tests/lint and focused regressions; sanitized public source/history/metadata scans;
reviewed allowlisted synchronization; deployment verification where needed; unchanged
accounts, schedules and media unless explicitly authorized. Record outcomes here
only after the corresponding gate passes. Never include private labels, owner IDs,
feed URLs, credential paths, raw newsletters or rejected model responses.

Chronological details: `TRUST_AND_POLISH_PLAN.md`. Runtime policy: `RUNTIME_REVIEW.md`.
