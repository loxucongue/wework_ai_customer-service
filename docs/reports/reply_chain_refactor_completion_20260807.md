# Reply Chain Refactor Completion Report

## Scope

Branch: `codex/reply-chain-refactor`

This report covers the structural completion state of the parallel Gate/Tool Planner/Reply refactor. It does not approve merging to `main`, deployment, or sending real customer messages.

## Architecture Result

The target shadow architecture is now auditable end to end:

- Shared context provides complete timed chat and authoritative facts.
- SOP Chat Gate is limited to content candidates and route suggestions.
- Tool Planner is limited to read-only tool planning and missing fact declarations.
- Read-only tool executor remains shadow/no external calls in this branch.
- Join only combines Gate, tool facts, and authoritative facts.
- Reply handoff marks Reply as the final customer-visible business brain.
- Commit shadow keeps writes after final reply validation.
- Behavior switch remains blocked by default.

## Constitution Boundary

The new normalizer boundary audit classifies Planner normalizer logic into:

- fact hard boundaries
- data cleanup
- soft prompt hints
- semantic overreach

Current result: `semantic_overreach_count=0`.

The audit explicitly guards against old or invalid patterns including:

- order-required-before-payment-card rules
- customer-visible internal payment-entry wording
- code deciding whether the current sales rhythm should resend a store card
- old-card references such as asking the customer to find a previous payment card

This keeps ordinary customer psychology, objection handling, and sales rhythm with the model, while preserving hard safety checks such as visible store IDs, paid-state no-card protection, health-risk holds, amount consistency, and one payment card per turn.

## Completion Audit

Added:

- `ai_paths/scripts/audit_planner_normalizer_boundaries.py`
- `ai_paths/scripts/audit_reply_chain_refactor_completion.py`
- tests for both audits

Latest completion audit result:

- Architecture components: valid
- Semantic ownership: passed
- Normalizer boundary: passed
- Tool Planner only-ready: passed
- Join final message owner: `reply`
- Behavior switch: not requested and not allowed

Remaining release gates:

- full offline simulation report
- three-model matrix report
- payload isolation report
- business wording freeze report
- rollback evidence report
- human review approval

## Model Choice

Current candidate remains `gpt-5.4`.

Reason: the small matrix identified it as the only stable candidate. Claude and Gemini are not release candidates until the full matrix has no hard errors, no infrastructure failures, and no baseline regressions.

## Status

Structural refactor completion: passed.

Release readiness: not passed.

Behavior switch: blocked.

Deployment: not performed.

Main merge: not performed.

## Validation

Passed:

- `git diff --check`
- `py_compile` for changed Python audit files
- Core refactor suite: `264 passed`
- Offline simulation framework tests: `33 passed`
- Completion audit: `completion_passed=true`

Not run:

- Real three-model matrix. No `REFACTOR_MODEL_*` environment variables are present in this worktree session.

Known current business-suite failures:

- Store scope resilience has existing failures around geocode evidence shape for town/POI cases.
- SOP Event tests still expect older model-guarded platform-task behavior, while current business rule says `sop_platform_task` should forward platform actions directly.
- Reply output strategy has old assertions around code rewriting store lookup queries; this conflicts with the current refactor direction to avoid code over-owning ordinary store-card resend or sales rhythm decisions.

These failures are not introduced by this structural audit patch. They remain blockers for release readiness and should be resolved against current business rules before any behavior switch.
