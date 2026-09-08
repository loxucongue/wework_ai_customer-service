# SOP remove age limit and close frozen backlog

- status: in_progress
- base_sha: `39fd610a15e8b731c9feefb3ba7a52a6fb458d08`
- branch: `main`
- production_baseline: `ai-paths-unified-20260908-182248-95079fdf`

## Change contract

- Type: high-risk third-party SOP execution and production backlog closeout.
- Scope: remove the 1800-second first-attempt age rejection; freeze the previously inspected 38 pending legacy tasks; consume only those task IDs as status 70 without `messages` or any `msgId`; deploy control/reply/worker from one clean main commit.
- Non-goals: do not send any frozen task content, do not consume message content, do not change the delivery-uncertainty recovery timeout, database schema, Reply V3, or newly arrived platform tasks.
- Invariants:
  - Task age alone never prevents a fresh deterministic gate evaluation or original-content send.
  - The delivery-uncertainty timeout remains in place to prevent a second proactive send when the first call result is unknown.
  - Frozen backlog closeout calls `/event/trigger/consume` with only `taskId`, `status=70`, and a remark; `messages` and `contentExhausted` are absent.
  - The frozen set is exactly 38 task IDs reconstructed from the first verified snapshot by its four status buckets and oldest-first ordering; any later task is excluded.
- Risk: status 70 is irreversible upstream; an incorrect task set could close a new valid task.
- Validation: deterministic unit tests, full test suite, payload dry validation, server release checks, frozen-set hash/count verification, per-task consume responses, and final pending reconciliation.
- Rollback: restore the previous unified release for code; platform status 70 cannot be rolled back, so closeout is limited to the frozen verified set.
