# Reply Chain Refactor Main Sync Audit

Date: 2026-08-07

## Result

- Latest main reviewed: `be65e329f`
- Refactor branch head after sync: `660e5e6b3`
- Merge commit: `1e6154dd2`
- `main` is an ancestor of `codex/reply-chain-refactor`.
- The refactor branch contains every main commit after base `625061d28` and keeps the parallel Gate/Tool Planner/Reply refactor work on top.
- No deployment or production message send was performed.

## Main Changes Migrated

| Area | Main commits | Migrated behavior |
| --- | --- | --- |
| Knowledge facts | `7531d38dd` | Teaching and cooperation questions use authoritative knowledge facts. |
| Store delivery | `a2a569308`, `e112114df`, `5eecc4e5d` | Multiple valid city fallback stores can be delivered; ranked store facts are preferred; Python no longer rewrites store queries or auto-appends cards. |
| Need and case flow | `b400b1874` | S1 need answers move into the case-fact flow instead of extended online questioning. |
| First-day outreach | `67b48d79d`, `dc2fb890f`, `93e44cf9a`, `b1439c137` | First-day outreach is split into stages, personalized from context, and refined before delivery. |
| Outreach observability | `08350d279`, `201dc3336`, `762170c5f`, `ceab59a31`, `c32c28241`, `9e570b589` | First-day run logs, empty states, preflight failures, refund-policy audit, backfill, and compact UI filters are present. |
| Duplicate prevention | `01e027364` | Duplicate first-day outreach cycles are blocked. |
| Effect evidence | `4083f3f8c`, `674da4114` | Single-session objections are recovered and effect questions require current authoritative case-image evidence. |
| Offer wording | `7e37365c4` | Proactive offers no longer expose the old original price. |
| Appointment operations | `b5d22711a`, `be65e329f` | Appointment blockers, silent-outreach handling, APIs, persistence, and operations dashboard are included. |

## Conflict Decisions

- Kept main's removal of Python store-query rewriting. Store meaning and whether a store tool is relevant remain model-owned.
- Kept main's complete case-image, teaching/cooperation, payment, outreach, and appointment facts.
- Retained refactor short-confirm and parallel-chain contracts.
- Added rule authority layers without restoring superseded transaction or store behavior:
  - `hard_law`
  - `business_fact`
  - `strong_default`
  - `playbook`
  - `deprecated`
- Changed mainline progression wording from unconditional to a strong default. Trust challenges, complaints, health risks, explicit refusal, repeated time uncertainty, and active busy context may pause progression.

## Additional Contract Fixes

The merged main tests exposed three implementation gaps, fixed on the refactor branch:

1. Activity images are no longer appended by Python based on stage keywords. SOP configuration or model output must explicitly select the media.
2. Raw top-level `store_id` and `confirmed_store_id` no longer authorize a customer-visible store card. Cards require structured store, appointment, or customer-scope facts.
3. City-level geocoding no longer inherits a default district merely because the customer query is a sentence longer than four characters.

## Verification

- Python full regression: `1367 passed, 2 skipped`
- Main feature regression: `243 passed`
- Prompt and Reply contracts: `373 passed`
- Parallel refactor and shadow gates: `262 passed`
- Model-semantics and rollback audits: `9 passed`
- Frontend TypeScript: passed
- Frontend ESLint: passed
- Next.js production build with Webpack: passed, 37 routes generated

The repository build wrapper requires WSL on Windows, and the default Turbopack build inferred the wrong workspace root because of an unrelated user-level lockfile. The equivalent Webpack production build completed successfully.

## Remaining Release Boundary

This sync proves source and deterministic-test completeness. It does not approve the parallel behavior switch. The refactor remains isolated until the full real-model matrix, offline simulation acceptance, human reply review, rollback evidence, and behavior-switch bundle gates all pass for the same commit.
