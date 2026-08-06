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
- hard errors: `0`
- failed critical scenarios: `[]`
- semantic pass rate: at least `0.90`
- production writes: `0`
- all sends captured only in virtual outbox

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
- postcommit shadow bundle audit ready;
- diagnostics ready for human review;
- offline simulation report passing;
- explicit human approval for branch and commit;
- rollback plan and no deployment from this branch.

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

