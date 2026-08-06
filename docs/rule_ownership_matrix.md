# Reply Chain Rule Ownership Matrix

This is the Stage 0 baseline for `codex/reply-chain-refactor`. It is not a new
business policy source. It maps existing active rule areas to their current and
target owners so the refactor can move responsibilities without losing rules or
moving business semantics into code.

Status values:

- `active`: still valid business or safety rule.
- `merged`: valid but should be expressed through a broader target contract.
- `superseded`: historical rule that must not be restored.
- `hard_boundary`: deterministic structure, safety, or fact protection owned by code.

| rule_id | source | business meaning | current owner | target owner | fact dependencies | type | migration status | regression tests |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| constitution_model_semantics | AGENTS.md project contract | Model decides customer semantics, psychology, and sales rhythm. | Global prompts, Planner, Reply, SOP Event | Global contract shared by Gate/Reply; Tool Planner only receives factual intent from Gate/Reply | Full timestamped conversation, authoritative facts | hard_boundary | active | prompt contract, simulation constitution checks |
| code_fact_boundaries | AGENTS.md project contract | Code handles factual inputs, tools, schema, idempotency, safety, fallback. | Normalizer, validation, tool nodes, runtime | Protocol pre-router, read-only tool executor, validation/commit layer | Tool facts, state facts, schema | hard_boundary | active | deterministic validation and isolation tests |
| full_chat_priority | refactor discussion and current pain points | Latest customer message and complete recent chat must dominate stale profile summaries. | Planner/Reply payloads, current_turn_context | Shared context builder used by Gate, Tool Planner, Reply | Full timestamped conversation, current_time | active | active | shadow context tests, simulation history-order cases |
| no_soft_profile_authority | test_context_slim_and_availability.py, refactor plan | Soft profile strategy must not override the current chat. | Planner payload pruning, Reply background facts | Shared authoritative snapshot; profile only as low-priority background if explicitly allowed | Customer profile, history events | active | active | context slimming tests |
| gate_not_business_brain | parallel refactor plan | Gate may select content candidates and route, but complex customer status, final turn outcome, and closing action belong to Reply. | SOP Chat Gate, Planner, Reply split | Gate content router + Reply final brain | Full timestamped conversation, gate evidence refs, tool facts | hard_boundary | active | reply chain refactor contract, gate router tests |
| sop_mainline_progression | SOP Event rules and sales mainline | Active outreach should advance the earliest unfinished mainline stage unless current context proves it is covered or unsafe. | SOP Event model and normalizer | SOP Event stays owner; Gate only routes chat-time SOP usage | SOP progress, recent sent facts, chat evidence | active | active | sop_event_flow, offline simulation |
| precision_answer_then_mainline | precision QA prompts/config | Answer precise objections first, then naturally connect one unfinished mainline action. | Gate/Planner/Reply prompts | Gate may select content candidate; Reply owns final phrasing and mainline action in complex cases | Precision QA config, current message, history | active | active | precision QA runtime contract, Reply model cases |
| offer_activity_facts | business_rules.json offer | Activity facts are 268 total, 10 prepay, 258 tail, limited quota, eligible registration gift, refundable if not done or unsatisfied, and approved scarcity reasons only. | business_rules, Planner/Reply prompts, SOP packs | Shared authoritative business facts; Reply chooses one fitting reason; validation protects amounts | business_rules.offer, payment state, sent facts | active | active | prompt contract, business rule model matrix, payment tests |
| project_scope_boundary | business_rules.json offer/customer_visible_evidence_policy | Online activity includes face/hand spot and pigment concerns including acne marks/pits and explicit mole-improvement direction; unsupported items such as wrinkle, eye bag, dark circle and water-light cannot be booked or charged. | business_rules, precision QA, Reply prompt | Gate may select precision answer; Reply finalizes scope answer and mainline bridge; validation blocks unsupported payment | current message, image facts, business_rules.offer | active | active | precision QA runtime contract, reply output strategy |
| effect_case_image_evidence | AGENTS long-term lesson, business_rules evidence policy | Customer asking effect/case should receive real case image when no recent authoritative image delivery exists; SOP completion or text promise does not prove image sent. | Planner prompt, kb tool plan, Reply | Tool Planner plans kb_search; Reply uses only tool case facts | sent_message_summary.case_image_delivery, kb_search facts | active | active | case image delivery regression, business rule model matrix |
| store_visible_scope_only | store fact integrity and long-term lessons | Store cards must come only from the customer's visible store range. | Store tools, Planner guards, Reply validation | Read-only tool executor + validation; Reply uses only provided store facts | store_scope_summary, store_resolution_fact | hard_boundary | active | store visibility and location-card tests |
| store_candidate_count_rule | business rule discussion | If visible candidates are 1-3, send all; if more than 3, ask district/location. | Planner/Reply prompt and store guards | Reply final brain using tool facts; Tool Planner only plans lookup | store_resolution_fact, visible candidates | active | active | store scenario simulation |
| store_after_card_mainline | Reply rules | After a real store card, do not keep asking convenience; handle objection then return to spot/case/activity as appropriate. | Reply prompt | Reply | Recent store card facts, history | active | active | door-store simulation cases |
| location_detail_disclosure | recent address policy | Public store address can be sent; detailed arrival guidance requires authoritative detail/registration flow and must not be fabricated. | Reply prompt, store validation | Reply + validation | Store detail facts, public address facts | active | active | location detail tests |
| payment_no_order_precondition | AGENTS long-term lesson | Activity quote/paving allows payment card; order is not a card precondition. | business_rules, Planner/Reply, payment guards | Reply final action + validation; backend order link after payment | Activity completion, payment state, risk state | active | active | payment card and transaction tests |
| payment_after_paid_registration | business rules | After paid, collect name/phone and visit intent; current ordinary flow does not query slots or create order_plan. | Planner/Reply/tool rules | Reply + deferred write commit layer | deposit_state, registration_state | active | active | transaction flow tests |
| unknown_message_transfer_paid | business rule | Platform unknown message type is an authoritative transfer-paid event. | Input normalization, payment evidence | Protocol pre-router/input normalization | msgtype/content placeholder | hard_boundary | active | unknown transfer tests |
| one_payment_card_per_turn | reply structure guard | One reply turn may contain at most one payment_collection. | Reply validation/postprocess | Validation/commit layer | reply_messages | hard_boundary | active | reply output strategy tests |
| health_risk_priority | business rules | Current allergy/inflammation/broken skin risk blocks sales push and payment card. | Risk evidence, Planner, Reply validation | Shared facts + Reply; validation enforces hard block | image_info, current message, risk_hold | hard_boundary | active | health risk simulation |
| explicit_reject_no_payment | business rules | Clear refusal/complaint/refund stops payment push; gentle outreach may be separate SOP Event decision. | Planner/Reply/SOP Event | Reply for chat; SOP Event for outreach | Current message, recent history | active | active | refusal simulation |
| human_wechat_style | business_rules identity and Reply quality rules | Replies should sound like a real WeChat salesperson: short, direct, warm, one action; no robotic phrases such as continuing to process, arranging next step, or abstract system reminders. | Reply prompt and soft quality checks | Reply owns final expression; soft quality warnings guide repair without clearing safe replies | current message, history, selected content, tool facts | active | active | reply output strategy, model simulation review |
| sop_platform_task_passthrough | SOP platform task rule | `sop_platform_task` message_content is platform-authored and should pass through without model rewriting. | SOP platform task service | Protocol pre-router / SOP service | event payload | hard_boundary | active | sop_platform_task_flow |
| model_failure_neutral_fallback | runtime rules | If model/repair fails, return the configured neutral wait text instead of empty reply. | Runtime, Reply node | Runtime fallback layer | failure trace, retry state | hard_boundary | active | model timeout tests |
| old_order_paid_window | AGENTS long-term lesson | Paid orders within 3 months protect paid state; older completed/paid history is not a current paid state. | Order normalization, payment facts | Authoritative fact snapshot + validation | order created_at/status/prepay_paid | hard_boundary | active | order lifecycle tests |
| order_required_before_payment_card | historical transaction rule | Requiring successful order before card is obsolete and must not be restored. | Historical docs/tests only | None | n/a | superseded | superseded | payment no-order regression |

Review notes:

- New architecture commits must update this matrix when moving an active rule.
- A rule may move owners only with a named target owner and a regression test.
- Business wording changes belong in a separate business-review commit, not in
  structural refactor commits.

## Structural Refactor Review Gate

These review gates are mandatory for every commit that changes the reply-chain
architecture. They are not business rules. They are release controls to keep the
refactor aligned with the project constitution.

| gate_id | purpose | required evidence |
| --- | --- | --- |
| rule_matrix_delta_review | Every moved or split responsibility must keep its active business rule mapped. | Updated row in this matrix, named target owner, and a regression test reference. |
| payload_isolation_review | Shadow-only diagnostics must not influence active Gate, Planner, or Reply prompts. | `workflow_tests/test_reply_chain_shadow_payload_isolation.py` passes and any new shadow field is listed there. |
| authority_snapshot_review | The shared context must prove complete timestamped chat is primary authority, the timeline window follows the full-chat/truncation policy, the current request message is present as the latest authoritative customer message, soft profile strategy is excluded, and authoritative fact sections have no source errors before any behavior switch. | `reply_chain_authority_audit_v1`, `reply_chain_timeline_window_audit_v1`, `reply_chain_current_message_audit_v1`, and `reply_chain_fact_snapshot_audit_v1` are present in shadow diagnostics; `workflow_tests/test_reply_chain_shadow_context.py` and `workflow_tests/test_parallel_reply_chain_shadow.py` pass. |
| gate_commit_boundary_review | Gate preview/router output must remain candidate-only. SOP task creation, `send_once` updates, database writes, and customer sends belong to the post-Reply validation/commit phase. | `chat_gate_commit_boundary_v1` is present in Gate preview/router shadow diagnostics; `workflow_tests/test_chat_gate_preview.py`, `workflow_tests/test_chat_gate_router_shadow.py`, and `workflow_tests/test_parallel_reply_chain_shadow.py` pass. |
| branch_input_isolation_review | Parallel Gate and Tool Planner branches must start from copied branch inputs and must not consume prior branch-output shadow fields when behavior switching is considered. | `parallel_branch_input_isolation_audit_v1` is present in runner diagnostics; no `shadow_only_fields_present_in_initial_state` are allowed for active parallel input; `workflow_tests/test_parallel_reply_chain_runner.py` and `workflow_tests/test_parallel_reply_chain_diagnostics.py` pass. |
| final_expression_owner_review | Join must not become a third brain. Complex turns must route to Reply as final customer-message owner; direct replies are only static Gate candidates with no dynamic fact requirement and still require commit validation. | `reply_final_expression_boundary_v1` is present in Join diagnostics; `workflow_tests/test_reply_chain_join_shadow.py` and `workflow_tests/test_parallel_reply_chain_shadow.py` pass. |
| direct_reply_guard_review | Gate direct reply is an explicit narrow exception, not a general business brain path. | `reply_chain_direct_reply_guard_audit_v1` proves static candidate exists and no dynamic facts, read tools, or unknown tools are required; `workflow_tests/test_reply_chain_join_shadow.py`, `workflow_tests/test_parallel_reply_chain_shadow.py`, `workflow_tests/test_parallel_reply_chain_diagnostics.py`, and `workflow_tests/test_reply_chain_shadow_bundle_audit.py` pass. |
| reply_handoff_readiness_review | Reply must receive complete timestamped chat, a ready timeline window, authoritative facts, Gate candidates as references, read-only tool facts, Join ownership evidence, a complete legacy-field mapping, and an explicit target Reply input schema before any Reply payload switch. | `reply_final_brain_handoff_readiness_audit_v1` is present, ready, includes `reply_chain_timeline_window_audit_v1`, includes `reply_legacy_field_mapping_audit_v1`, includes `reply_final_brain_target_input_schema_audit_v1`, and has no blockers; `workflow_tests/test_reply_final_brain_handoff.py` and `workflow_tests/test_parallel_reply_chain_shadow.py` pass. |
| reply_target_input_schema_review | Future Reply active payload groups must be explicit and must not include legacy Planner groups or sources. | `parallel_reply_chain_shadow.current_serial_observation.reply_target_input_schema_audit_schema` is `reply_final_brain_target_input_schema_audit_v1`, target schema is `reply_final_brain_target_input_schema_v1`, no active group id/source points at legacy Planner fields, and ready is true; `workflow_tests/test_parallel_reply_chain_diagnostics.py` and `workflow_tests/test_reply_final_brain_handoff.py` pass. |
| reply_handoff_semantic_residue_review | Legacy Planner customer wording and sales-decision fields must be explicitly migrated or blocked before Reply payload switching. | `parallel_reply_chain_diagnostics_v1.migration.reply_handoff_legacy_business_field_count` is observed as `0`; nonzero counts create `reply_handoff_legacy_business_field_residue:*` blockers; `workflow_tests/test_parallel_reply_chain_diagnostics.py` passes. |
| commit_phase_shadow_review | Final writes, memory records, trace/run persistence, deferred write tools, and customer-visible assistant-message commits must be observable as a post-Reply validation commit phase before any write-path refactor. | `reply_chain_commit_shadow_v1` and `reply_chain_deferred_write_handoff_audit_v1` are present in saved runtime state, included in final diagnostics, and excluded from model payloads; `workflow_tests/test_reply_chain_commit_shadow.py`, `workflow_tests/test_platform_reply_runtime.py`, `workflow_tests/test_parallel_reply_chain_diagnostics.py`, and `workflow_tests/test_reply_chain_shadow_payload_isolation.py` pass. |
| business_wording_freeze_review | Structural commits must not silently change customer-visible business facts or sales wording. | Diff review confirms no unrelated edits to `business_rules.json`, SOP pack text, or precision QA text; intentional wording changes use a separate business-review commit. |
| model_semantics_ownership_review | Code, Join, and Tool Planner must not take ownership of customer psychology, objections, or sales rhythm. | Reviewer confirms ordinary sales semantics remain in model prompts/Reply decisions, with code limited to facts, tools, schema, safety, and fallback. |
| simulation_regression_review | Behavior flags cannot be enabled until old and new chains are compared on representative conversations. | Offline simulation report covers SOP, precision QA, store, payment, paid registration, risk, and model-failure cases. |
| rollback_evidence_review | Each stage must be independently revertible and must not be deployed from this branch. | Commit hash recorded for the stage, no deployment command run, and `codex/reply-chain-refactor` remains separate from `main`. |

Before any behavior flag changes from shadow mode to active mode, the reviewer
must check all fifteen gates above and attach the test output or report path in the
commit or review note.

`reply_chain_release_review_checklist_v1` is diagnostic evidence only. It can
show which gates have automated shadow evidence, but it must always require
human review and offline simulation evidence before any behavior switch.
The checklist must also group unresolved blockers by review owner, including
contract, runner, comparison, commit, migration, Reply payload schema, and
manual review. These groups are for human audit readability only and must not
be treated as automatic approval to switch behavior.

`reply_chain_behavior_switch_guard_v1` is the final admission guard after the
fifteen review gates. It consumes the flag snapshot, postcommit shadow bundle
audit, comparison diagnostics, offline simulation report, and human review
approval. It is not itself one of the fifteen diagnostic gates, and it must not
enable runtime behavior by side effect. `workflow_tests/test_reply_chain_behavior_switch_guard.py`
must pass before any proposed behavior switch can be reviewed.
When diagnostics include grouped release blockers, the guard may expose those
groups for reviewer readability, but the groups remain evidence only and do not
enable behavior by themselves. If a group still reports blockers, the guard
must treat that as unresolved review evidence and block behavior switching even
when the flat gate list is accidentally empty.
The postcommit shadow bundle audit must apply the same unresolved-group
protection so `ready_for_refactor_review` cannot disagree with the final switch
guard on release-review blockers.
If a release checklist ever reports `can_enable_behavior_switch` as anything
other than `false`, both the postcommit bundle audit and final guard must block
it as malformed evidence. Only the final behavior-switch guard may produce a
positive switch decision after human review and offline simulation evidence are
present.

Parallel behavior cannot be enabled until `SOP_CHAT_GATE_V2_ENABLED`,
`TOOL_PLANNER_V2_ENABLED`, and `REPLY_FINAL_BRAIN_V2_ENABLED` are all true and
the comparison diagnostics show no shadow replay diffs. This prevents Gate or
Tool Planner from becoming the final business brain by accident.
