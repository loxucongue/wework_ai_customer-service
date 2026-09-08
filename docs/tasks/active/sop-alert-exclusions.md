# Third-party SOP alert exclusions

- Goal: stop classifying customer relation deletion, human takeover, and unconfirmed send results as SOP send-failure alerts.
- Non-goals: do not change send blocking, strict sequence, recovery, platform consume, database schema, or customer messaging.
- Base SHA: `a6a5e902`.
- Production baseline: verify immediately before rollout.
- Branch: `main`.
- Scope: SOP failure alert classifier, deterministic tests, and the single-task runbook.
- Risk: suppressing a broader reason than intended could hide actionable send failures; match only stable status/reason codes.
- Validation: classifier matrix, relevant SOP tests, full repository tests, production health and alert-state verification.
- Rollback: unified production release immediately preceding rollout.
