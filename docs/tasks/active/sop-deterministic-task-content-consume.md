# SOP deterministic task and content consumption

- status: implementation_verified
- base_sha: `d289d3473974af656c0f43e76a768d0122550ef6`
- branch: `codex/sop-deterministic-task-content-consume`
- production_baseline: verify before deployment

## Change contract

- Type: high-risk third-party SOP execution contract correction.
- Scope: online-service pending polling, three deterministic customer gates, delayed `sop-messages` lookup, direct original-content sending, and atomic task/message result payloads on `/event/trigger/consume`.
- Non-goals: Reply V3, mainline sales follow-up, database schema, external endpoint names, and historical-task replay.
- Invariants:
  - Only `customer_unopened && relation_active && ai_auto_reply` may query and send the next SOP message group.
  - The model, customer/order context, transition copy, and content selection are not used on this path.
  - A normally returned proactive-send call consumes exactly the selected `msgId` with status `30` together with task status `30`.
  - Any no-send or deterministic failure consumes only the task with status `70`; it never submits a `messages` result.
  - `contentExhausted` is not inferred and remaining message groups are not consumed.
- Risk: incorrect task/message identifier pairing can skip content or duplicate sends.
- Validation: deterministic client payload tests, end-to-end service tests for all gate branches and failures, SOP regression suite, Ruff, then full tests.
- Rollback: revert the single implementation commit and redeploy the preceding clean release.

## Progress

- Confirmed upstream consume contract supports `messages: [{msgId,status,remark}]` in the same request as task status.
- Confirmed only the first unconsumed message group is sent and only its `msgId` is consumed.
- Polling now loads only `/pending`; `/sop-messages` is called only after all three customer gates pass.
- The normal execution path no longer calls a model, rewrites content, waits for delivery callbacks, claims task status `20`, or consumes compatibility tasks as a side effect.
- Client-side invariants reject task `30` without exactly one explicit `msgId=30`, and reject message results on task `70`.
- Legacy execution recovery is terminalized as task `70` without sending; manual resend is disabled because terminal platform tasks cannot safely change back to `30`.
- Verification: `523 passed` from `pytest -q tests`; focused SOP suite `48 passed`; Ruff and `git diff --check` passed.
- Deployment pending explicit release authorization and production baseline verification.
