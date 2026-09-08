# SOP business terminal and failure boundary

- status: ready_to_deploy
- base_sha: `01542bf45d9fc92a499280a00d90d9b97d2704d1`
- branch: `main`
- production_baseline: `ai-paths-unified-20260908-192853-46439957`

## Change contract

- Type: urgent third-party SOP execution-state and alert-boundary correction.
- Scope: keep customer opened, customer deleted, and human takeover as handled task `70` outcomes without failure alerts; keep technical, remote-interface, qualification/data, identity, content, and send failures unconsumed and recoverable; classify alerts by ownership; emit at most one alert per task lifecycle/incident; deploy control/reply/worker from one clean main commit.
- Non-goals: do not change `/sop-messages` first-group selection, successful task `30 + msgId=30`, database schema, Reply V3, message delivery protocol, or the frozen 38-task closeout.
- Invariants:
  - A technical failure must never be represented as task `70` or `completed_without_send`.
  - A failed or incomplete earlier task remains unresolved and blocks later content.
  - Customer opened, deleted, and human takeover consume only the task as `70`; they never consume a `msgId` and never create a failure alert.
  - A successful proactive-send call remains the only path to task `30` and one explicit sent `msgId`.
  - An unknown send result remains unconsumed and must not trigger a second proactive send.
- Risk: changing terminal failures back to recoverable work can retain bad third-party payloads indefinitely; alerts must expose ownership and missing fields without leaking customer content or credentials.
- Validation: deterministic execution/failure/alert tests, full suite, payload assertions, clean release checks, and production observation with worker initially stopped.
- Rollback: restore `ai-paths-unified-20260908-192853-46439957`; do not modify previously closed upstream task states during rollout.

## Validation evidence

- Focused deterministic boundary suite: `59 passed`.
- Repository test suite: `571 passed`, with one pre-existing dependency deprecation warning.
- Ruff and diff whitespace checks: passed.
- Production preflight: control and reply active; worker intentionally inactive; current release `ai-paths-unified-20260908-192853-46439957`.
