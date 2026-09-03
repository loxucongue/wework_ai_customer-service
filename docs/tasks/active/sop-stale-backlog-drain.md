# SOP stale backlog drain

- Type: production hotfix and controlled cleanup
- Base SHA: `8c736d4871bb4f3e7e9be96fe7dbd96458b47a65`
- Production baseline: `8c736d4871bb4f3e7e9be96fe7dbd96458b47a65`, clean main release
- Goal: drain SOP tasks older than 10 minutes without sending customer messages, while preserving task-only consumption, strategy callback, and local audit.
- Non-goal: change live-task ordering, merge message content, consume content msgIds, or increase customer-send concurrency.
- Modules: SOP platform polling and terminal failure tests.
- Contract: stale tasks consume only platform taskId as status 70, report `sop_send_failed`, and never call the customer send interface.
- Risk: upstream pending, consume, and strategy APIs currently exhibit connect timeouts.
- Verification: deterministic tests, production health, pending-count trend, per-endpoint latency and error samples.
- Rollback: previous release `ai-paths-unified-20260903-8c736d48`.
