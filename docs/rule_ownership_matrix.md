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
| sop_mainline_progression | SOP Event rules and sales mainline | Active outreach should advance the earliest unfinished mainline stage unless current context proves it is covered or unsafe. | SOP Event model and normalizer | SOP Event stays owner; Gate only routes chat-time SOP usage | SOP progress, recent sent facts, chat evidence | active | active | sop_event_flow, offline simulation |
| precision_answer_then_mainline | precision QA prompts/config | Answer precise objections first, then naturally connect one unfinished mainline action. | Gate/Planner/Reply prompts | Gate may select content candidate; Reply owns final phrasing and mainline action in complex cases | Precision QA config, current message, history | active | active | precision QA runtime contract, Reply model cases |
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
| sop_platform_task_passthrough | SOP platform task rule | `sop_platform_task` message_content is platform-authored and should pass through without model rewriting. | SOP platform task service | Protocol pre-router / SOP service | event payload | hard_boundary | active | sop_platform_task_flow |
| model_failure_neutral_fallback | runtime rules | If model/repair fails, return the configured neutral wait text instead of empty reply. | Runtime, Reply node | Runtime fallback layer | failure trace, retry state | hard_boundary | active | model timeout tests |
| old_order_paid_window | AGENTS long-term lesson | Paid orders within 3 months protect paid state; older completed/paid history is not a current paid state. | Order normalization, payment facts | Authoritative fact snapshot + validation | order created_at/status/prepay_paid | hard_boundary | active | order lifecycle tests |
| order_required_before_payment_card | historical transaction rule | Requiring successful order before card is obsolete and must not be restored. | Historical docs/tests only | None | n/a | superseded | superseded | payment no-order regression |

Review notes:

- New architecture commits must update this matrix when moving an active rule.
- A rule may move owners only with a named target owner and a regression test.
- Business wording changes belong in a separate business-review commit, not in
  structural refactor commits.
