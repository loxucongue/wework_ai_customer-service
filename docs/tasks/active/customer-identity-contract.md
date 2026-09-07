# Customer identity contract repair

- Status: ready for review
- Base SHA: `6060eb8cb46ee0e791fb37096caa9a3b3060a711`
- Production baseline: `9b8ba02839b0597a708476e848d025f7f75ceeb6`
- Branch: `codex/identity-contract-fix`

## Goal

Stop customer identifier types from being substituted for one another, make platform-customer verification explicit, keep sales-contact scope stable, and expose precise identity labels in operational views.

## Non-goals

- Do not rewrite production customer data in this change.
- Do not resend, consume, or replay SOP tasks.
- Do not change V3 sales semantics or model prompts.

## Scope

- V3/workflow-compatible request normalization and validation.
- Conversation/takeover/customer-context identity propagation.
- SOP task ingestion, send identity, dedupe, and strategy callbacks.
- Customer-scoped storage lookup and administration endpoints.
- Operational identity labels and deterministic regression tests.

## Invariants

- `corp_id`, `wechat`, `external_userid`, `platform_customer_id`, `platform_user_id`, and `customer_add_wechat_id` are distinct identifiers.
- An external contact ID must never be written or sent as a platform customer ID.
- A platform customer ID used by platform-only APIs must have an explicit or platform-lookup source.
- Sales-contact persistence remains scoped by `corp_id + wechat + external_userid`; missing production scope fails closed.
- Historical mixed rows remain readable during migration but are never used as authoritative identity evidence.

## Validation

- Identity contract and request-normalization unit tests.
- V3 lifecycle, SOP platform, outreach, storage, and dashboard regression tests.
- Frontend type check, lint, and production build.
- Clean branch review before push and PR.

## Rollback

Revert the merge commit and return to `main@6060eb8c`; schema changes, if any, must be additive and backward compatible.

## Progress

- [x] Baseline and production release verified.
- [x] Identity model and ingress validation implemented.
- [x] Runtime, SOP, storage, and UI callers migrated.
- [x] Regression suite passed.
- [x] PR created: https://github.com/loxucongue/wework_ai_customer-service/pull/1

## Evidence

- Backend: `405 passed`.
- Frontend: TypeScript check, changed-file ESLint, Next.js production build, and server bundle passed.
- Migration chain: Alembic head is `20260907_02`.
- Review: `git diff --check` passed; historical mixed rows remain read-only.
