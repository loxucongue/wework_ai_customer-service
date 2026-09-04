# Local closing catalog and intent/emotion audit

- Type: V3 business configuration and architecture audit
- Branch: `codex/local-closing-catalog-intent-audit`
- Base SHA: `6111981665171bea9630bf5892cec8c387923b65`
- Production baseline: not changed by this task; deployment is out of scope.
- Goal: provide versioned local JSON for closing rules, strategies, and scripts when the upstream business catalog is unavailable, while documenting the actual intent, emotion, and routing implementation.
- Non-goal: add a second sales decision engine, add keyword-based intent rules, send delayed messages, change transaction authority, or deploy to production.
- Exclusive scope: `ai_paths/app/policies/ai_closing_catalog_v1.json`, closing-catalog loading and source selection in `ai_paths/app/services/`, related settings, focused V3 closing tests, `docs/contracts/sales-strategy.md`, `docs/interfaces/external.md`, and the intent/emotion contract added by this task.
- Shared-boundary note: the separate SOP stale-backlog task owns SOP worker/platform polling and terminal persistence files; this task does not modify them.
- Invariants: V3 Reply remains the only final sales-semantic decision; local JSON is candidate knowledge only; customer state remains isolated by `corp_id + wechat + external_userid/customer_id`; explicit opt-out and safety guards override closing candidates.
- Risk: treating an empty upstream catalog as an outage can accidentally activate provisional local business content. Source mode and provenance must therefore be explicit and observable.
- Verification: deterministic loader/source-selection tests, existing closing integration/policy/analytics tests, configuration parsing, and documentation review.
- Rollback: disable the local catalog source or revert this task commit; no database migration is planned.
