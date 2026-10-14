# Canonical work plan

Updated: 2026-10-14. This is the single backlog, not a release diary.
The agreed order prioritizes speed, safety, privacy and zero-cost operation.
Statuses: pending / in progress / released / awaiting observation / needs user.
Keep IDs stable; every scope, order or status change must have a dated reason below.
Do not silently drop tasks, create new ones from old chronological notes, or claim
production improvement from tests alone. Private deployment/run evidence stays local.

| ID | Priority | Agreed work | Status | Acceptance / next action |
|---|---:|---|---|---|
| T01 | 1 | Precise script validation and targeted correction | Released | Fixed rules/locations, existing retry only, no rejected text in logs. Observe future failures. |
| T02 | 2 | Smaller model requests without losing evidence | Released | Compact schema, post-selection scores omitted; selected facts and original supports retained. |
| T03 | 3 | Measure patch/context efficiency in actual runs | Awaiting observation | Numeric request/response sizes, retry timing and exact context sharing shipped; equivalent-content ordinary runs must establish savings. |
| T04 | 4 | Profile Kokoro without changing output quality | Awaiting observation | Fine-grained timing and offline tool shipped; local bounded experiment incomplete. Equivalent Linux throughput still needed before tuning. |
| T05 | 5 | Fit optional newspaper within protected execution budget | Awaiting observation | Implementation validated with synthetic regressions; whole paper phase bounded, dynamic completion reserve, clear deferred reason. Verify adoption in ordinary runs, not an extra episode. |
| T06 | 6 | Reconcile execution time and provider consumption | Pending | Include setup, failures, repairs and shutdown; distinguish measured generation from billed minutes/actual balances. Provider-panel access may be needed. |
| T07 | 7 | Investigate newsletters without extracted news | Pending | Explain observed gaps, especially unknown reasons; do not treat absence of extraction as absence of useful news. |
| T08 | 8 | Perceptual naturalness and name pronunciation | Pending | Compare specific excerpts, isolate script/preparation/voice; technical metrics alone are insufficient. Listener feedback may be needed. |
| T09 | 9 | Review real newspaper readability and visual usefulness | Pending | Semantic non-repetition, complete sentences, useful visuals and readable two-page target. Use existing approved editions. |
| T10 | 10 | Physical mobile/accessibility validation | Needs user | iPhone/PWA, keyboard, VoiceOver and compact player; synthetic browser tests are not device certification. |
| T11 | 11 | Maintainability and remaining privacy procedures | Pending | Further module separation; reviewed feed-link rotation procedure only with explicit operator authorization. Preserve existing controls. |

## Change log

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
