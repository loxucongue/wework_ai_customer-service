# SOP Monitor Navigation

- Goal: move the SOP processing overview into a standalone management BI under Monitor, keep the platform task page focused on evidence, and label the legacy event page as a low-level audit log.
- Non-goals: change SOP pull, decision, send, consume, retry, callback, strategy writeback, storage schema, or production credentials.
- Base SHA: `e69770bb`.
- Production baseline: `20260907-133305-5043dbf3`; all four services active at task start.
- Branch: `codex/sop-monitor-navigation`.
- Ownership: frontend SOP analytics/log pages, app navigation, focused frontend tests and current/contract docs.
- Change contract: frontend-only read-only presentation split; low runtime risk; type-check, lint, production build, code review, desktop/mobile browser QA; rollback to the production baseline above.
- Completed: task registered and baseline verified; standalone `/analytics/sop` BI added under Monitor; task and low-level event logs relabeled and separated; desktop/mobile browser QA, type-check, full frontend lint and production build passed. Review fixes prevent pre-load zero metrics, pin BI timestamps to Asia/Shanghai, and contain the low-level event page on mobile.
- Remaining: commit, final review, merge to main, deploy and production verification.
