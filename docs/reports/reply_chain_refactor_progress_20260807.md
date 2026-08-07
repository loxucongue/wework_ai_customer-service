# Reply Chain Refactor Progress - 2026-08-07

## Scope

Branch: `codex/reply-chain-refactor`

Base evidence commit before this working set: `880b48c4425bcaaaf7831fe41f29d3bbbc6f13c2`

This report covers the current unmerged refactor branch only. It is not a production deployment report and does not approve switching traffic to the new chain.

## Current Direction

The target architecture remains:

- SOP Chat Gate and Tool Planner run as bounded, parallel evidence producers.
- Gate selects SOP, precision QA, and simple content candidates, but does not become the final business brain.
- Tool Planner plans read-only fact acquisition and does not write customer-facing copy.
- Reply is the final customer-visible expression brain for normal and complex turns.
- Code only handles input normalization, factual evidence, tool execution, schema, idempotency, safety boundaries, retries, and non-business fallback.

## Changes In This Working Set

- Fixed local LinkAI relay connectivity for Python `httpx` by defaulting model HTTP clients to `trust_env=False`.
- Kept `trust_env=True` as an explicit opt-in setting for environments that require system proxy variables.
- Strengthened prompt contracts for short confirmations after store/location context:
  - Do not reopen store matching.
  - Do not ask the city or district again.
  - Do not resend store cards.
  - Use one text reply and naturally continue the next mainline action, such as asking spot duration or type.
- Removed the previous over-protective direction:
  - Reply validation no longer hard-fails a store card only because the current turn is not about stores.
  - Planner normalizer no longer deletes a model-planned store lookup just because it appears stale.
  - Available Reply model remains the final expression path; Planner direct reply is only a fallback when Reply is unavailable or fails.
- Updated simulation fixture semantics so the short-confirmation store scenario allows a light mainline follow-up instead of incorrectly requiring a pure stop.

## Constitution Check

Compliant:

- No new Python keyword branch was added to decide customer psychology, objection type, sales stage, or normal sales rhythm.
- Store card truth, customer-visible store scope, payment amount, paid state, health-risk hold, and schema boundaries remain code-level facts.
- The short-confirmation behavior is controlled through model prompt and simulation semantics, not through hard business gating.

Risk reduced:

- The previous `store_card_requires_current_turn_support` hard validation could force neutral fallback even when a model could repair the reply. It has been removed from final Reply validation.
- The previous stale-store tool deletion in Planner normalizer could override model tool planning. It has been removed.

Remaining risk:

- Older normalizer code still contains many hard repairs from previous production fixes. Those need separate review before enabling the new parallel chain.
- The main refactor architecture is not active yet; current production-shaped behavior is still serial with shadow/audit components.

## Deterministic Validation

Passed:

```text
git diff --check
python -m py_compile touched runtime/prompt files
pytest selected refactor/model/reply/store tests: 29 passed, 1 warning
```

Selected tests included:

- `test_model_client_uses_five_second_connect_timeout`
- `test_model_client_can_opt_into_environment_proxy_settings`
- `test_valid_planner_direct_reply_still_uses_final_reply_model_when_available`
- `test_valid_planner_draft_with_non_visible_tool_policy_violations_only_after_reply_failure`
- `test_short_non_location_message_does_not_get_tool_deleted_by_normalizer`
- `test_reply_validation_allows_scope_backed_store_card_without_current_turn_business_gate`
- `test_planner_and_reply_short_ack_after_store_context_do_not_reopen_store`
- `test_refactor_model_matrix.py`

## Model Validation

### Focused Scenario

Scenario: `store_v2_non_location_short_message`

Goal: Customer says "好的" after recent store/location context. The system must not reopen store matching or resend store cards, but may lightly continue the mainline.

OpenAI only run:

```text
Artifact: .tmp_runtime/simulation/model_matrix_openai_short_ack_20260807_2205/matrix_result.json
Model: gpt-5.4
Attempts: 5
Hard errors: 0
Semantic pass rate: 100%
Infrastructure failures: 0
P50/P90: 22.357s / 28.607s
```

Representative replies:

```text
好嘞，地区我先记下了。您脸上斑点大概多久了？
好的，龙湾区我先记着。您这个斑点大概多久了？
好嘞，龙湾区我先按您这边记着。您脸上的斑点大概多久了？
```

### Three-Model Focused Matrix

Artifact: `.tmp_runtime/simulation/model_matrix_3models_short_ack_20260807_2220/matrix_result.json`

| Profile | Model | Semantic Pass | Hard Errors | Infra Failures | P50 | P90 | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| openai | `gpt-5.4` | 100% | 0 | 0 | 21.509s | 22.987s | Best focused result |
| claude | `claude-opus-4-7` | 0% | 1 | 0 | 64.548s | 70.903s | Too slow; one neutral fallback |
| gemini | `gemini-3.5-flash` | 0% | 3 | 3 | 9.231s | 10.110s | Fast but unusable through current relay setup |

### Three-Model Cross-Scenario Smoke

Artifact: `.tmp_runtime/simulation/model_matrix_3models_cross_smoke_20260807_2235/matrix_result.json`

Scope: 12 store-related scenarios, 1 attempt each.

| Profile | Model | Semantic Pass | Hard Errors | Infra Failures | P50 | P90 | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| openai | `gpt-5.4` | 100% on evaluable samples | 1 | 1 | 29.063s | 32.793s | Best current candidate |
| claude | `claude-opus-4-7` | 50% on evaluable samples | 10 | 7 | 65.793s | 95.288s | Not suitable for this chain now |
| gemini | `gemini-3.5-flash` | 0% | 12 | 12 | 8.998s | 11.263s | Relay/model integration failing |

OpenAI's one flagged cross-smoke scenario was `store_v2_composed_location`. The customer-visible reply was acceptable: it used the prior province/city/district plus the customer's road name, sent the real store card, and continued the mainline. This looks like evaluator or scenario accounting mismatch rather than a visible reply failure.

## Model Recommendation

Current recommendation for this branch:

1. Use `gpt-5.4` as the primary model for continued refactor validation.
2. Do not select `claude-opus-4-7` for this chain yet: latency is high and recovery/fallback behavior is unstable.
3. Do not select `gemini-3.5-flash` yet: current relay calls repeatedly produce infrastructure failures despite low latency.

This is based on focused and cross-scenario smoke only. Full release-gate simulation is still required before any behavior switch.

## Remaining Work

- Review the existing Planner normalizer for older over-protective business repairs and classify them as:
  - hard fact boundary,
  - data cleanup,
  - soft model guidance,
  - or model-semantics overreach.
- Run full offline simulation, not just focused/cross-smoke subsets.
- Run broader OpenAI-only effect simulation across SOP, precision QA, payment, paid registration, health risk, rejection, and model recovery.
- Calibrate stale semantic reviewers where they contradict accepted business rhythm, especially "answer then continue one mainline action".
- Produce a switch-readiness bundle only after hard errors are zero in the full critical set and manual review agrees with the sampled customer-visible replies.

## Current Status

The branch has made measurable progress, but the refactor is not complete and must not be merged or deployed yet.
