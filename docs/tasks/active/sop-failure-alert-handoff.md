# Third-party SOP failure alert and platform handoff

- Goal: document the current third-party SOP flow at one-task granularity and alert DingTalk for every task that lacks confirmed send evidence.
- Non-goals: do not replay historical tasks, change external SOP/send schemas, add a database migration, or move execution to the third-party platform in this change.
- Base SHA: `883b183b05c0aaec8d3c3809678c3c92454d9494`.
- Production baseline: `/opt/ai-paths/releases/ai-paths-unified-20260908-135308-883b183b`.
- Branch: `codex/sop-failure-alert-handoff`.
- Scope: SOP service/config/runtime wiring, a DingTalk alert client, third-party SOP contracts/interfaces/runbook, and temporary deterministic verification outside Git.
- Invariants:
  - Only confirmed send evidence is success; every other execution result is a failure or unresolved failure.
  - Failure alerting must never consume platform content, release later sequence content, or send customer messages.
  - Webhook access token and signing secret come only from environment variables and never enter Git, logs, task payloads, or documentation.
  - Repeated recovery polls must not flood the group; one task/reason has a bounded repeat interval while reason changes remain visible.
- Validation: compile/import, temporary signed-request contract tests, task-result classification matrix, mocked alert delivery/retry/dedupe, production health and one explicit synthetic rollout alert.
- Rollback: production baseline above.

## Work log

- Registered ownership and verified clean latest main plus production baseline.
- Documented the real per-task interface and decision chain, including the existing AI path for customers who already replied and the `useAiCopy` migration gap.
- Added a signed DingTalk robot client and a durable `sop_failure_alert` event flow with per-task/category/reason dedupe and retry backoff.
- Alert classification treats every result without confirmed send evidence as failure. Confirmed customer delivery is persisted before consume/rule-data callbacks, so downstream callback failure cannot trigger a duplicate customer send or false “not sent” alert.
- Integrated alerts at pull/page errors, missing content, persistence, queue/recovery exceptions, all non-sent results, delivery callbacks and manual resend failures. Alert failure never changes SOP state.

## Validation evidence

- `python -m compileall -q ai_paths/app`: passed.
- Ruff on all changed Python modules: passed.
- Temporary alert contract matrix under ignored `artifacts/`: passed for signed URL construction, confirmed-send suppression, accepted/unconfirmed, no-send, retry, shadow, dedupe and alert retry.
- Real SQLite repository persistence/dedupe check: passed without schema changes.
- Relevant SOP deterministic tests: 31 passed.
- Full repository test suite: 480 passed, one existing dependency deprecation warning.
- Production rollout and one explicitly labeled synthetic DingTalk alert: pending.
