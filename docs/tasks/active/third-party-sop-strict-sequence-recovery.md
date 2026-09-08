# Third-party SOP strict sequence and recovery

- Goal: prevent later SOP content from sending before every earlier content group has a confirmed successful send, and keep failed/unknown sends recoverable without consuming platform content.
- Non-goals: do not change customer reply V3, payment, store, appointment, or external API schemas.
- Base SHA: `bafdf4306ecdc67801c4d9c2b6036afe82b8f0b4`.
- Production baseline: `/opt/ai-paths/releases/ai-paths-unified-20260908-115412-08b7dd49`.
- Branch: `codex/sop-strict-sequence-recovery`.
- Scope: `sop_platform_task_service.py`, `sop_platform_client.py`, SOP persistence/recovery tests, third-party SOP contract and release state.
- Invariants:
  - Do not send a later content group when an earlier group was not confirmed sent.
  - Send failure or unknown submission must not consume the platform task as `30` or `70`.
  - Platform terminal reconciliation must stop retry loops without inventing send success.
  - Content-group identity and trigger-task identity remain distinct and auditable.
- Validation: deterministic sequence, multi-group, delivery uncertainty, recovery backoff/terminal reconciliation, admin dedupe; full test suite and Ruff.
- Rollback: current production release above.

## Implemented

- Each customer batch exposes only its earliest content group to the model; a non-send or non-earliest selection records `send_failed` and consumes neither `30` nor `70`.
- Durable contact-scoped ordering blocks later tasks behind any earlier unresolved task, including restart and manual-resend paths.
- Definite send failure preserves the claimed platform task/content as `send_failed/platform_failed`; submission uncertainty waits for callback or matching conversation evidence.
- Every no-send gate, including human takeover, deleted customer and quiet hours, now preserves the earliest content as `send_failed/platform_sequence_blocked` and never writes platform `70`; a paired trigger is completed as `30` only after its content has confirmed send evidence.
- Recovery queries honor persisted exponential backoff, terminal platform states stop hot-loop retries, and historical retry requests are reduced to the strict client schema.
- `/sop-messages` contributes only `nextGroup` per due trigger; task-log aggregation deduplicates trigger/content views and no longer adds content-group counts to task counts.

## Validation evidence

- Targeted Ruff: passed for all changed Python and test files.
- Targeted deterministic tests: `31 passed`.
- Full deterministic suite after rebasing latest `main`: `479 passed, 1 warning`.
- Repository-wide Ruff is not a release gate for this change because latest `origin/main` already contains 52 unrelated unused-import/local-variable findings in V3/first-day modules; no changed file has a Ruff finding.
