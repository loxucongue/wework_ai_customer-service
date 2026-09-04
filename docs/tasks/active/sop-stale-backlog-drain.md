# SOP stale backlog drain

- Type: production hotfix and controlled cleanup
- Base SHA: `8c736d4871bb4f3e7e9be96fe7dbd96458b47a65`
- Production baseline: `8c736d4871bb4f3e7e9be96fe7dbd96458b47a65`, clean main release
- Goal: drain SOP tasks older than 10 minutes without sending customer messages, while preserving task-only consumption, strategy callback, and local audit.
- Non-goal: change live-task ordering, merge message content, consume content msgIds, or increase customer-send concurrency.
- Modules: SOP platform polling and terminal failure tests.
- Contract: stale tasks consume only platform taskId as status 70, report `sop_send_failed`, and never call the customer send interface.
- Current cleanup policy: all non-priority accounts are consumed as `humantakeover`; `SL0906`, `DY8808`, `SL1580`, `SL2478`, and `SL8004` retain the normal task path.
- Priority policy: poll those five accounts first on every iteration and use all remaining queue capacity for bulk human-takeover work.
- Restart recovery: durable `platform_queued` events are replayed after worker restarts; terminal audit records separate consume, strategy callback, and local-finalization timestamps.
- Risk: upstream pending, consume, and strategy APIs currently exhibit connect timeouts.
- Verification: deterministic tests, production health, pending-count trend, per-endpoint latency and error samples.
- Latency correction: worker wall-clock spans were polluted by synchronous MySQL work blocking the asyncio event loop, so they are not evidence of upstream latency by themselves.
- Current hotfix: recovery scans and the active no-send/send terminal path run blocking repository calls in worker threads. Structured HTTP trace logs record path, task ID, response codes, total time, and transport phase timestamps; an event-loop watchdog records lag separately without tokens or message content.
- Test evidence: `143 passed` on 2026-09-04, including explicit HTTP timing-log redaction coverage.
- Rollback: previous release `ai-paths-unified-20260903-8c736d48`.
