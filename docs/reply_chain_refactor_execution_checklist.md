# Reply Chain Refactor Execution Checklist

This document is the execution checklist for `codex/reply-chain-refactor`.
It does not introduce business rules. It defines how to develop, review, and
test the refactor without losing existing rules or drifting away from the
project constitution.

## 1. Non-Negotiable Boundaries

- Work only on `codex/reply-chain-refactor`.
- Do not commit to `main`.
- Do not deploy from this branch.
- Do not send real customer messages.
- Do not call production write APIs from refactor or simulation work.
- Do not add Python keyword branches for normal sales semantics, objections,
  customer psychology, or sales stage.
- Structural commits must not silently edit `business_rules.json`, SOP pack
  wording, precision QA wording, or customer-visible payment/store policies.

The constitution remains:

- Model owns business semantics, customer psychology, sales rhythm, and final
  customer-visible expression.
- Code owns factual input preparation, read tools, schema, idempotency, safety
  boundaries, validation, retries, fallback, persistence, and audit evidence.

## 2. Target Responsibility Split

The refactor must not turn Gate into the new brain. The intended split is:

| Node | Owns | Must not own |
| --- | --- | --- |
| Shared Context | Full timestamped chat, current message, authoritative facts, visibility scope, structured payment/order/SOP facts. | Customer psychology, closing strategy, or final wording. |
| SOP Chat Gate | Content candidates: SOP pack, precision QA, simple static scene candidate, and route suggestion. | Final customer intent, sales rhythm, tool parameters, database writes, send_once, or final reply for complex turns. |
| Tool Planner | Read-only dynamic fact needs and tool arguments. | Customer wording, SOP choice, closing move, or objection handling. |
| Read-only Tool Executor | Execute approved read-only tools and return facts. | Write APIs, business interpretation, or customer-facing text. |
| Deterministic Join | Merge Gate candidates and tool facts, decide whether direct reply is structurally allowed. | A third business brain, closing strategy, or new wording. |
| Reply | Final expression, complete history understanding, current customer intent, one natural next action when appropriate. | Fabricating facts, bypassing safety boundaries, or writing state directly. |
| Commit Coordinator | Persistence, virtual/real outbox, deferred writes after validation, audit. | Business semantics or customer-visible text generation. |

## 3. Development Batches

Each batch must be independently revertible and have its own commit.

| Batch | Purpose | Primary review question | Required proof |
| --- | --- | --- | --- |
| B0 Baseline | Freeze current branch and rule ownership. | Do all active rules have owners? | Rule matrix and contract tests pass. |
| B1 Shared Context | Build complete timestamped chat and authoritative fact snapshot. | Does latest chat dominate stale summaries? | Context shadow tests pass. |
| B2 Gate Shadow | Gate emits candidates and routing only. | Did Gate become a business brain? | Gate preview/router tests pass. |
| B3 Tool Planner Shadow | Tool planning is read-only and factual. | Did Tool Planner generate wording or sales decisions? | Tool plan and read-only executor tests pass. |
| B4 Join Shadow | Join merges facts and ownership evidence. | Did Join become a third brain? | Join and handoff tests pass. |
| B5 Reply Handoff | Reply receives complete target input. | Is Reply still final expression owner and free of legacy Planner wording residue? | Handoff tests and diagnostics pass. |
| B6 Parallel Runner | Gate and Tool Planner run in parallel shadow mode. | Are branch inputs isolated and comparison auditable? | Runner, comparison, diagnostics, and bundle audit pass. |
| B7 Offline Simulation | Multi-turn simulation proves behavior does not regress. | Do real problem combinations still pass without touching production? | Offline simulation report with zero hard errors and critical scenarios passing. |
| B8 Behavior Switch Review | Decide whether to activate new chain later. | Is there explicit human approval, rollback plan, and evidence? | Behavior switch guard passes only with all required evidence. |

## 4. Code Review Arrangement

Every non-trivial batch has two reviews.

### 4.1 Structure Review

Reviewer checks:

- The changed files match the batch scope.
- New schema fields have a version, purpose, and source.
- Shadow-only fields do not enter active model prompts.
- Parallel branches receive copied inputs and do not consume each other's
  shadow outputs.
- `tool_plan_preview.migration_audit` is present and proves zero legacy
  customer-visible or sales-semantic residue before any behavior switch review;
  bundle gates must not rely only on copied observation fields.
- No production write operation can run before final Reply validation.
- Failures are observable through node name, blocker, fallback source, and
  retry/failure metadata.
- Direct reply remains a narrow structural exception:
  - static candidate exists;
  - no read tools are required;
  - no dynamic facts are missing;
  - no payment, store, risk, registration, refund, or complex history judgment
    is involved.

### 4.2 Business Rule Protection Review

Reviewer checks:

- `docs/rule_ownership_matrix.md` still maps every active and hard-boundary
  rule to an owner.
- No active rule was deleted to reduce prompt length or code complexity.
- Any moved rule has a target owner and regression test.
- Superseded rules stay superseded, especially:
  - order is not a precondition for payment card;
  - old payment card restrictions must not be restored.
- High-risk rules are covered in tests:
  - customer visible store scope only;
  - 1-3 visible store candidates are sent as cards;
  - same turn has at most one payment card;
  - paid, health risk, complaint/refund, clear refusal, and over-four-person
    boundaries remain protected;
  - three-month paid order protection window remains intact;
  - current message and full timestamped chat dominate stale profile summaries.

## 5. Per-Commit Checklist

Before staging:

- `git status --short --branch` shows `codex/reply-chain-refactor`.
- Diff does not include deployment files, production env, tokens, or real
  customer data.
- Diff does not change business wording unless this is explicitly a separate
  business-review commit.
- New tests fail before the change or prove a new structural contract.
- The changed code has no new normal-sales keyword branch.

Before commit:

```powershell
git diff --check
$env:PYTHONPATH='ai_paths'
python -m py_compile <changed-python-files>
```

Then run the batch-specific tests from section 6.

Commit message format:

```text
refactor: <structural change summary>
```

If a commit intentionally changes customer-visible wording, do not use this
structural format. Split it into a separate business-review commit.

## 6. Test Nodes

### T0 Contract And Payload Isolation

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_reply_chain_refactor_contract.py `
  workflow_tests/test_reply_chain_refactor_settings.py `
  workflow_tests/test_reply_chain_shadow_payload_isolation.py -q
```

Purpose:

- Constitution and rule ownership are still enforced.
- Shadow diagnostics do not enter active model payloads.

### T1 Shared Context

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest workflow_tests/test_reply_chain_shadow_context.py -q
```

Purpose:

- Current message is present as the latest authoritative customer message.
- Full timestamped chat is preferred over profile summaries.
- Authoritative facts are present and source-audited.

### T2 SOP Chat Gate

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_chat_gate_preview.py `
  workflow_tests/test_chat_gate_router_shadow.py -q
```

Purpose:

- Gate only selects candidates and route suggestions.
- Gate cannot create SOP tasks, send messages, write send_once, or act as final
  brain for complex turns.

### T3 Tool Planner And Read-Only Execution

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_tool_plan_preview.py `
  workflow_tests/test_read_only_tool_executor_shadow.py -q
```

Purpose:

- Tool Planner outputs facts-to-fetch, not customer-visible wording.
- Only read-only tools are allowed in the early parallel phase.

### T4 Join And Reply Handoff

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_reply_chain_join_shadow.py `
  workflow_tests/test_reply_final_brain_handoff.py -q
```

Purpose:

- Join does not become a third brain.
- Reply receives complete target input and ownership evidence.

### T5 Commit Phase

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_reply_chain_commit_shadow.py `
  workflow_tests/test_platform_reply_runtime.py -q
```

Purpose:

- Writes remain after Reply validation.
- Test-isolated paths reduce side effects without being treated as business
  success.

### T6 Parallel Runner And Diagnostics

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_parallel_reply_chain_runner.py `
  workflow_tests/test_parallel_reply_chain_shadow.py `
  workflow_tests/test_parallel_reply_chain_comparison.py `
  workflow_tests/test_parallel_reply_chain_diagnostics.py `
  workflow_tests/test_reply_chain_shadow_bundle_audit.py `
  workflow_tests/test_reply_chain_external_gate_evidence.py `
  workflow_tests/test_reply_chain_behavior_switch_guard.py -q
```

Purpose:

- Parallel branch inputs are isolated.
- Comparison and diagnostics are evidence only.
- Release review cannot approve behavior switching by itself.

### T7 Offline Full-Chain Simulation

Fast structural check:

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest workflow_tests/test_full_chain_simulation.py -q
```

Target effect check:

```powershell
$env:PYTHONPATH='ai_paths'
python ai_paths/scripts/run_full_chain_simulation.py `
  --fixture workflow_tests/fixtures/full_chain_simulation_v1.json `
  --attempts 3 `
  --critical-attempts 5 `
  --concurrency 2
```

Required report:

- `schema_version=offline_reply_chain_simulation_report_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `evaluation_scope.schema_version=offline_simulation_scope_v1`
- `evaluation_scope.full_release_gate_candidate=true`
- `evaluation_scope.targeted_smoke=false`
- `run_options.schema_version=offline_simulation_run_options_v1`
- `run_options.skip_review=false`
- `run_options.attempts >= 3`
- `run_options.critical_attempts >= 5`
- `scenario_summary` includes every scenario
- every non-critical scenario has at least `3` attempts
- every critical scenario has at least `5` attempts
- `review_artifacts.result_count >= attempt_count`
- hard errors: `0`
- `summary.infrastructure_failures=0`
- `summary.acceptance.infrastructure_failures_zero=true`
- `coverage.schema_version=offline_simulation_coverage_audit_v1`
- `coverage.missing_required_categories=[]`
- `coverage.missing_critical_required_categories=[]`
- `summary.acceptance.scenario_coverage_complete=true`
- failed critical scenarios: `[]`
- semantic pass rate: at least `0.90`
- `safety.production_customer_messages_sent=false`
- `safety.production_writes_allowed=false`
- `safety.virtual_outbox_only=true`
- `safety.production_write_count=0`
- all sends captured only in virtual outbox
- `isolation_audit.schema_version=offline_simulation_isolation_summary_v1`
- `isolation_audit.passed=true`
- `isolation_audit.result_count >= scenario_count`
- `isolation_audit.missing_result_count=0`
- `isolation_audit.failed_result_count=0`
- `isolation_audit.run_dirs_under_tmp_simulation=true`
- `isolation_audit.paths_within_run_dir=true`
- `isolation_audit.connector_urls_simulation_only=true`
- `isolation_audit.adapters_simulation_only=true`
- `isolation_audit.identity_simulation_scoped=true`
- `isolation_audit.real_connector_credentials_present=false`
- `review_artifacts.schema_version=offline_simulation_review_artifacts_v1`
- `review_artifacts` includes request/event IDs, node trace names, tool call names, virtual outbox counts, and simulated write counts for human review
- `effect_review.schema_version=offline_simulation_effect_review_v1`
- `effect_review.result_count >= attempt_count`
- `effect_review.items` contains customer input excerpts, AI reply excerpts, scores, and reviewer reasons for every low-score, hard-error, or infrastructure-error sample selected for manual review

Candidate model matrix:

```powershell
$env:PYTHONPATH='ai_paths'
$env:REFACTOR_MODEL_RELAY_BASE_URL='https://linkai.shop'
$env:REFACTOR_MODEL_CLAUDE_API_KEY='<local-only secret>'
$env:REFACTOR_MODEL_GEMINI_API_KEY='<local-only secret>'
$env:REFACTOR_MODEL_OPENAI_API_KEY='<local-only secret>'
python ai_paths/scripts/run_refactor_model_matrix.py `
  --profiles claude,gemini,openai `
  --attempts 3 `
  --critical-attempts 5 `
  --concurrency 2 `
  --profile-timeout-seconds 120 `
  --require-keys
```

The runner defaults to `--attempts 3 --critical-attempts 5` so a full run is
release-gate shaped by default. Lower values are allowed only for targeted
smoke or debugging, and those reports must not be used as model-selection
evidence for behavior switching.

The matrix currently compares:

- `claude-opus-4-7`
- `gemini-3.5-flash`
- `gpt-5.4`

The LinkAI root URL is accepted for operator convenience, but the runner
normalizes it to the OpenAI-compatible API base `https://linkai.shop/v1`
before calling `/chat/completions`. A root URL must never be used directly as
the API base, because it returns the LinkAI web UI HTML with HTTP 200 and would
make JSON calls fail as `JSONDecodeError`.

The keys must only live in local or server environment variables. Do not write
them into committed tests, fixtures, reports, Markdown, or `.env` files.

Required matrix report:

- `schema_version=reply_chain_refactor_model_matrix_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `relay_base_url=https://linkai.shop/v1`
- `evaluation_scope.schema_version=reply_chain_refactor_model_matrix_scope_v1`
- `evaluation_scope.full_release_gate_candidate=true`
- `evaluation_scope.targeted_smoke=false`
- `run_options.schema_version=reply_chain_refactor_model_matrix_run_options_v1`
- `run_options.skip_review=false`
- `run_options.attempts >= 3`
- `run_options.critical_attempts >= 5`
- `profiles_requested` includes `claude`, `gemini`, and `openai`
- each profile is `completed`
- each completed profile has `model_profile.protocol=openai-compatible relay` and `model_profile.api_key_value_logged=false`
- no profile has `status=timed_out`
- each completed profile has `profile_summary.semantic_pass_rate`, `p50_ms`, and `p90_ms`
- each completed profile has `profile_summary.effect_issue_count`, `effect_low_score_count`, and `effect_hard_or_infra_count`
- `ranking` includes every completed profile and exposes semantic pass rate, P90, and effect review counts for human model selection
- any accepted profile has `profile_summary.infrastructure_failures=0`
- at least one profile has `profile_summary.accepted_by_release_thresholds=true`
- `safety.api_keys_written_to_report=false`
- `safety.production_customer_messages_sent=false`
- `safety.production_writes_allowed=false`

Before any behavior-switch review, attach the matrix report to
`reply_chain_behavior_switch_guard(model_matrix_report=...)`. The release
diagnostics may list `model_matrix_review` as missing, but a valid matrix report
is the authoritative evidence that proves that gate. If the report is missing,
incomplete, skipped because of absent keys, or contains any safety marker other
than the values above, the guard must block.

The final behavior-switch guard requires every external evidence report listed
in this section. Omitting payload isolation, business wording freeze, rollback
evidence, or model semantics ownership evidence must block the switch even when
offline simulation, model matrix, diagnostics, shadow bundle, and human review
are present.

When reviewing postcommit shadow evidence, recompute
`reply_chain_shadow_bundle_audit(..., simulation_report=..., model_matrix_report=..., payload_isolation_report=..., business_wording_freeze_report=..., rollback_evidence_report=..., model_semantics_ownership_report=...)`
with the same offline simulation, model matrix, payload isolation, business
wording freeze, rollback evidence, and model semantics ownership reports. This
keeps the postcommit bundle and final behavior-switch guard aligned: unresolved
diagnostic gates remain blockers, while externally proven gates are cleared only
by valid reports.
In short, the postcommit bundle and final behavior-switch guard aligned state is required before review.
The older two-report review call form
`reply_chain_shadow_bundle_audit(..., simulation_report=..., model_matrix_report=...)`
is still valid for simulation/model matrix evidence alone, but it is incomplete
for final behavior-switch review because it does not prove wording freeze or
rollback/no-deploy evidence.

The postcommit shadow bundle audit and parallel diagnostics must both expose
the reviewed `git_commit`. The final behavior-switch guard blocks if the human
review commit differs from the shadow bundle, diagnostics, offline simulation,
or model matrix evidence, or if the shadow bundle/diagnostics commit evidence is
missing.

After running the matrix, scan changed files and reports for secrets before
committing:

```powershell
rg -n -P 'sk-[A-Za-z0-9]{20,}|REFACTOR_MODEL_.*_API_KEY=(?!<local-only-|[''"]?dummy_).+' docs workflow_tests ai_paths .tmp_runtime
```

Business wording freeze audit:

```powershell
$env:PYTHONPATH='ai_paths'
python ai_paths/scripts/audit_business_wording_freeze.py `
  --base-ref main `
  --head-ref HEAD `
  --report .tmp_runtime/business_wording_freeze_audit.json
```

Required freeze report:

- `schema_version=reply_chain_business_wording_freeze_audit_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `include_worktree=true` for pre-commit local audits, or `--committed-only` only after the reviewed commit is final and the worktree is clean
- `changed_protected_paths=[]`
- `customer_visible_business_assets_unchanged=true`
- `review_required=false`
- `safety.audit_only=true`
- `safety.does_not_change_runtime_behavior=true`
- `safety.does_not_send_customer_messages=true`
- `safety.does_not_write_database=true`
- `safety.does_not_call_models=true`

This audit is a structural freeze check only. If it reports protected path
changes, do not hide that by editing the report. Split business wording changes
into a separately reviewed business commit, or keep the behavior switch blocked.

Payload isolation audit:

```powershell
$env:PYTHONPATH='ai_paths'
python ai_paths/scripts/audit_reply_chain_payload_isolation.py `
  --head-ref HEAD `
  --report .tmp_runtime/payload_isolation_audit.json
```

Required payload isolation report:

- `schema_version=reply_chain_payload_isolation_audit_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `shadow_only_fields` is non-empty
- `payloads_checked` includes `planner`, `reply`, `sop_chat_gate_selector`, and `sop_chat_gate_messages`
- `leaked_fields_by_payload` has no leaked field entries
- `payload_isolation_passed=true`
- `active_model_payloads_checked=true`
- `safety.audit_only=true`
- `safety.does_not_change_runtime_behavior=true`
- `safety.does_not_send_customer_messages=true`
- `safety.does_not_write_database=true`
- `safety.does_not_call_models=true`

This audit proves shadow diagnostics stay out of active model inputs. It does
not prove reply quality, customer psychology, or business-rule correctness.

Model semantics ownership audit:

```powershell
$env:PYTHONPATH='ai_paths'
python ai_paths/scripts/audit_model_semantics_ownership.py `
  --head-ref HEAD `
  --report .tmp_runtime/model_semantics_ownership_audit.json
```

Required model semantics ownership report:

- `schema_version=reply_chain_model_semantics_ownership_audit_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `ownership_contract_checked=true`
- `tool_planner_must_not_own` includes `customer_visible_text`, `sales_psychology`, and `closing_move`
- `reply_owns` includes `final_customer_visible_messages`, `complex_turn_outcome`, and `single_mainline_action`
- `code_must_not_own` includes `normal_sales_intent`, `objection_psychology`, and `sales_rhythm`
- `tool_planner_legacy_residue_count=0`
- `tool_planner_only_ready=true`
- `join_final_expression_boundary_schema=reply_final_expression_boundary_v1`
- `join_final_customer_message_owner=reply`
- `join_generates_customer_visible_text=false`
- `join_decides_sales_psychology=false`
- `direct_reply_scope=static_candidate_only_no_dynamic_facts`
- `direct_reply_final_customer_message_owner=validated_static_gate_candidate`
- `direct_reply_requires_commit_validation=true`
- `reply_handoff_schema=reply_final_brain_handoff_shadow_v1`
- `reply_handoff_ready=true`
- `legacy_business_field_mapping_schema=reply_legacy_field_mapping_audit_v1`
- `unmapped_legacy_business_fields=[]`
- `semantic_ownership_passed=true`
- `safety.audit_only=true`
- `safety.does_not_change_runtime_behavior=true`
- `safety.does_not_send_customer_messages=true`
- `safety.does_not_write_database=true`
- `safety.does_not_call_models=true`
- `safety.does_not_call_external_tools=true`

This audit proves structural ownership boundaries only. It does not prove the
model understood the customer or that the final reply effect is good; those
remain covered by offline full-chain simulation and the model matrix.

Rollback/no-deploy evidence audit:

```powershell
$env:PYTHONPATH='ai_paths'
python ai_paths/scripts/audit_refactor_rollback_evidence.py `
  --base-ref main `
  --head-ref HEAD `
  --report .tmp_runtime/rollback_evidence_audit.json
```

Required rollback evidence report:

- `schema_version=reply_chain_refactor_rollback_evidence_v1`
- `git_commit` matches the reviewed behavior-switch commit
- `git_commit_set` contains exactly that commit
- `branch=codex/reply-chain-refactor`
- `branch_is_refactor=true`
- `main_branch_untouched=true`
- `changed_deployment_sensitive_paths=[]`
- `deployment_sensitive_paths_unchanged=true`
- `rollback_plan.schema_version=reply_chain_behavior_switch_rollback_plan_v1`
- `rollback_plan.restore_flags_to_shadow_or_disabled=true`
- `rollback_plan.revert_stage_commit=true`
- `rollback_plan.rerun_diagnostics_before_reenable=true`
- `rollback_plan.no_deployment_from_refactor_branch=true`
- `rollback_plan.rollback_steps` is non-empty
- `safety.audit_only=true`
- `safety.does_not_change_runtime_behavior=true`
- `safety.does_not_send_customer_messages=true`
- `safety.does_not_write_database=true`
- `safety.does_not_call_models=true`
- `safety.does_not_deploy=true`

This audit does not prove business quality. It only proves this refactor stage
is reviewable, revertible, and not mixed with deployment work. If deployment
sensitive files changed, split them out or keep the behavior switch blocked.

### T8 Core Regression Bundle

Run before any human review of behavior switch:

```powershell
$env:PYTHONPATH='ai_paths'
python -m pytest `
  workflow_tests/test_reply_chain_refactor_contract.py `
  workflow_tests/test_reply_chain_refactor_settings.py `
  workflow_tests/test_reply_chain_behavior_switch_guard.py `
  workflow_tests/test_reply_chain_shadow_context.py `
  workflow_tests/test_chat_gate_preview.py `
  workflow_tests/test_chat_gate_router_shadow.py `
  workflow_tests/test_tool_plan_preview.py `
  workflow_tests/test_read_only_tool_executor_shadow.py `
  workflow_tests/test_reply_chain_join_shadow.py `
  workflow_tests/test_reply_final_brain_handoff.py `
  workflow_tests/test_reply_chain_commit_shadow.py `
  workflow_tests/test_reply_chain_shadow_bundle_audit.py `
  workflow_tests/test_reply_chain_external_gate_evidence.py `
  workflow_tests/test_model_semantics_ownership_audit.py `
  workflow_tests/test_parallel_reply_chain_runner.py `
  workflow_tests/test_parallel_reply_chain_shadow.py `
  workflow_tests/test_parallel_reply_chain_comparison.py `
  workflow_tests/test_parallel_reply_chain_diagnostics.py `
  workflow_tests/test_reply_chain_shadow_payload_isolation.py `
  workflow_tests/test_platform_reply_runtime.py `
  workflow_tests/test_model_timeout_and_planner_payload.py `
  workflow_tests/test_full_chain_simulation.py -q
```

## 7. Release Review Evidence

Behavior switching remains blocked unless all evidence is present:

- flag snapshot with all required active flags;
- postcommit shadow bundle audit ready, with `git_commit` matching the reviewed
  commit;
- diagnostics ready for human review, with `git_commit` matching the reviewed
  commit;
- offline simulation report passing, with `git_commit` matching the reviewed
  commit;
- model matrix report passing, with `git_commit` matching the reviewed commit;
- payload isolation report passing, with `git_commit` matching the reviewed
  commit;
- model semantics ownership report passing, with `git_commit` matching the
  reviewed commit;
- rollback evidence report passing, with `git_commit` matching the reviewed
  commit;
- explicit human approval for branch, commit, and behavior-switch scope;
- reviewed rollback plan with flag-restore steps and no deployment from this
  branch.

The review evidence may say "ready for human review". It must not say "safe to
enable production" unless the final behavior-switch guard says so after human
approval.

## 8. How This Prevents Rule Loss

- Rule ownership matrix forces every active rule to keep an owner.
- Shadow payload isolation prevents diagnostics from changing model behavior.
- Gate direct-reply guard prevents Gate from becoming the brain.
- Tool Planner tests prevent factual lookup from becoming sales reasoning.
- Join tests prevent deterministic merge code from creating business wording.
- Reply handoff tests ensure the final business brain receives complete chat,
  Gate candidates, tool facts, and explicit blockers.
- Offline simulation catches multi-turn regressions that single-node tests miss.
