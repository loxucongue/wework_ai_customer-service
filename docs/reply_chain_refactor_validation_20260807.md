# Reply Chain Refactor Validation 2026-08-07

## Scope

Branch: `codex/reply-chain-refactor`

This stage validates the parallel Gate/Planner refactor evidence before any behavior switch. It does not enable the new chain, does not merge to `main`, does not deploy, and does not send real customer messages.

## Deterministic Checks

- `git diff --check`: passed
- `py_compile` for changed prompt/policy modules: passed
- Secret scan over `ai_paths`, `config`, `workflow_tests`, `docs`: passed
- Refactor/shadow guard suite before the prompt fix: `314 passed, 1 warning`
- Focused contract/config/simulation tests after the prompt fix: `106 passed, 1 warning`

## Model Matrix Smoke

Run directory: `.tmp_runtime/simulation/model-matrix-20260807-093136/`

This was a targeted one-case smoke, not a release gate.

| Profile | Model | Result | Hard Errors | Semantic Pass | P90 |
|---|---|---:|---:|---:|---:|
| openai | `gpt-5.4` | completed | 0 | 100% | ~23.1s |
| gemini | `gemini-3.5-flash` | provider failure | 1 | n/a | ~9.4s |
| claude | `claude-opus-4-7` | timeout/json failure | 1 | n/a | ~54.9s |

Recommendation from smoke only: keep `gpt-5.4` as the current validation baseline. Gemini and Claude need larger retry/fallback evidence before they can be considered reliable for this chain.

## Offline Simulation Evidence

All simulation used local adapters and local simulation DB/outbox only. No production platform writes or customer sends were performed.

Pre-fix category results:

| Category | Report | Hard Pass | Semantic Pass | Notes |
|---|---|---:|---:|---|
| 门店V2 | `.tmp_runtime/simulation/suite-20260807-093819/report.md` | 100% | 100% | 12 scenarios |
| 预约金 | `.tmp_runtime/simulation/suite-20260807-094633/report.md` | 100% | 100% | 8 scenarios |
| 已付登记 | `.tmp_runtime/simulation/suite-20260807-095424/report.md` | 100% | 100% | 4 scenarios |
| 健康风险 | `.tmp_runtime/simulation/suite-20260807-095927/report.md` | 100% | 100% | 4 scenarios |

SOP Gate regression found:

- Pre-fix report: `.tmp_runtime/simulation/suite-20260807-100743/report.md`
- Failure: activity already introduced, customer asked "那怎么报名", but the chain asked for participant count instead of cleanly handing off to Planner/Reply for 10 yuan payment card.

Fix applied in commit `7968a640f`:

- Removed stale activity SOP tail text: "自己一位参加吗/按人数登记".
- Clarified Chat Gate: after activity is already introduced, payment/signup questions must route `ai_only` to Planner/Reply, not to an SOP objection pack.
- Clarified Planner/Reply: ordinary single-person signup does not require participant-count confirmation; default 10 yuan card is allowed after activity quote is complete.
- Added contract/config tests and simulation expectation for this behavior.

Post-fix SOP Gate evidence:

- Report: `.tmp_runtime/simulation/suite-20260807-104034/report.md`
- Git commit in report: `7968a640f`
- Hard pass: `100%`
- Semantic pass: `100%`
- Infrastructure failures: `0`
- P50/P90: `45.2s / 49.5s`

## Current Status

The specific SOP Gate payment handoff regression is fixed and verified in offline simulation.

## Additional Validation Notes

Later validation on this branch found and fixed three evidence gaps before any behavior switch:

1. SOP Gate repeated the new-customer opening pack when the latest customer message was only a short acknowledgement after a recent location exchange. The guard now treats this as a structural stage-regression violation and asks the model to pick the next unfinished post-location mainline pack or route to ordinary AI when tool facts are needed.
2. `sop_platform_task` was still affected by legacy suppression and personalization branches. Per the current protocol contract, platform tasks now preserve `message_content` and pass through directly, while keeping hard payment safety checks.
3. City-level store lookup could be incorrectly narrowed by geocode default districts or text similarity. The store tool now separates the administrative level explicitly present in the customer's wording from lower-level geocode defaults, and text narrowing is only allowed for district/township/detail/store-name scopes.

Additional deterministic checks after these fixes:

- `git diff --check`: passed
- `py_compile` for changed Python modules: passed
- `workflow_tests/test_full_chain_simulation.py workflow_tests/test_sop_configuration_contract.py workflow_tests/test_prompt_refactor_contract.py workflow_tests/test_distance_origin_normalization.py workflow_tests/test_store_resolution_v2.py`: `128 passed, 1 warning`
- Store scope deterministic tests: `44 passed, 1 warning`

Additional offline simulation evidence:

| Scope | Report | Hard Pass | Semantic Pass | Notes |
|---|---|---:|---:|---|
| 门店 V2 after guard | `.tmp_runtime/simulation/suite-20260807-120703/report.md` | 100% | 100% | 13 evaluable attempts, critical pass |
| SOP Event after platform-task pass-through | `.tmp_runtime/simulation/sop_event_20260807_1237/report.md` | 100% | 87.5% | Remaining semantic failures are expected no-send scenarios that the reviewer scored as failure; hard behavior is correct |
| 广州多店 -> 番禺区 targeted | `.tmp_runtime/simulation/city_many_v01_20260807_1315/report.md` | 100% | 100% | Follow-up district sends store `301` |
| 荆州市 1-3 stores targeted | `.tmp_runtime/simulation/city_few_v01_20260807_1410/report.md` | 100% | 100% | City-level lookup keeps both visible city candidates |
| 湖北省 -> 荆州市荆州区 targeted | `.tmp_runtime/simulation/province_scope_v01_20260807_1410/report.md` | 100% | 100% | Province asks for city/district first, follow-up sends required stores |

Invalid or incomplete evidence:

- `.tmp_runtime/simulation/suite-20260807-104818/report.md` is invalid because model environment variables were missing.
- `.tmp_runtime/simulation/store_match_20260807_1320/` is incomplete because the local command hit a 20-minute timeout before generating `report.md`. Its checkpoint files were used only to identify failures, not as release-gate evidence.

This is still not enough evidence to enable the behavior switch. Missing release-gate evidence:

- Full three-model matrix with normal attempts `3` and critical attempts `5`.
- Full offline simulation across all categories after commit `7968a640f`.
- Baseline comparison against the current serial chain.
- Manual review of representative customer-visible replies.
- Rollback evidence bundle and final approval checklist.

Behavior switch remains blocked.
