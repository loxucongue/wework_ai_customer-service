from typing import Any, TypedDict


class ToolCall(TypedDict, total=False):
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    error: str


class TraceEntry(TypedDict, total=False):
    node: str
    started_at: str
    finished_at: str
    duration_ms: int
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]
    tool_calls: list[ToolCall]
    error: str | None


class AgentState(TypedDict, total=False):
    request_id: str
    customer_id: str
    corp_id: str
    content: str
    file_image: str | None
    image_urls: list[str]
    conversation_history: list[str]
    conversation_turns: list[dict[str, Any]]
    user_id: int | None
    wechat: str | None
    external_userid: str | None
    customer_add_wechat_id: str | int | None
    sales_contact_key: str
    global_customer_key: str
    customer_scope: dict[str, Any]
    confirmed_store_id: str | int | None
    confirmed_store_name: str | None
    store_id: str | int | None
    store_name: str | None
    appointment_id: str | int | None
    appointment_time: str | None
    request_context: dict[str, Any]
    test_isolated: bool
    memory_persist_allowed: bool
    runtime_budget: dict[str, Any]

    normalized_content: str
    location_card: dict[str, Any]
    image_info: dict[str, Any]
    guardrail_result: dict[str, Any]
    customer_profile: dict[str, Any]
    customer_basic_info: dict[str, Any]
    history_events: list[dict[str, Any]]
    lifecycle_stage: str
    appointment_cache: dict[str, Any]
    customer_context: dict[str, Any]
    customer_context_error: str | None
    customer_store_knowledge: dict[str, Any]
    sent_message_summary: dict[str, Any]
    sop_gate: dict[str, Any]
    sop_gate_decision: dict[str, Any]
    sop_gate_preview: dict[str, Any]
    sop_gate_router_shadow: dict[str, Any]
    sop_progress_evidence: dict[str, Any]
    background_substeps: list[dict[str, Any]]
    background_fact_views: dict[str, Any]
    reply_chain_shadow_context: dict[str, Any]
    store_context_status: str
    store_context_elapsed_ms: int
    store_context_skipped_steps: list[str]
    location_evidence: dict[str, Any]
    store_resolution_fact: dict[str, Any]
    input_quality_flags: list[str]
    model_deadline: dict[str, Any]
    model_context_metrics: dict[str, Any]
    recovery_attempts: list[dict[str, Any]]
    recovery_reason: str
    fallback_source: str

    planner_decision: str
    planner_stage: str
    planner_sub_rule_id: str
    conversion_stage: str
    customer_type: str
    main_blocker: str
    next_step: str
    payment_state: str
    payment_action: str
    payment_decision: dict[str, Any]
    store_binding_decision: dict[str, Any]
    order_decision: dict[str, Any]
    order_state: dict[str, Any]
    deposit_state: dict[str, Any]
    registration_state: dict[str, Any]
    appointment_state: dict[str, Any]
    appointment_decision: dict[str, Any]
    sales_progression: dict[str, Any]
    closing_move: dict[str, Any]
    precision_qa_decision: dict[str, Any]
    current_known_store: dict[str, Any]
    store_candidate: dict[str, Any]
    planner_reply_messages: list[dict[str, Any]]
    planner_tool_calls: list[dict[str, Any]]
    reply_constraints: list[str]
    primary_task: dict[str, Any]
    secondary_tasks: list[dict[str, Any]]
    turn_evidence: dict[str, Any]
    required_tools: list[dict[str, Any]]
    tool_policy_violations: list[dict[str, Any]]
    tool_plan_preview: dict[str, Any]
    read_only_tool_executor_shadow: dict[str, Any]
    reply_chain_join_shadow: dict[str, Any]
    parallel_reply_chain_shadow: dict[str, Any]
    reply_chain_refactor_flags: dict[str, Any]
    parallel_gate_planner_runner_shadow: dict[str, Any]
    parallel_reply_chain_diagnostics: dict[str, Any]
    parallel_reply_chain_comparison: dict[str, Any]
    reply_strategy: dict[str, Any]
    handoff: dict[str, Any]
    memory_update_hint: dict[str, Any]
    sop_stage: str
    sop_step: str
    sop_stage_rules: dict[str, Any]
    tool_results: dict[str, Any]
    fact_envelope: dict[str, Any]
    reply_messages: list[dict[str, Any]]
    case_image_send_record: dict[str, Any]
    store_fact_memory_record: dict[str, Any]
    planner_source: str
    policy_id: str
    policy_family_id: str
    exact_policy_id: str
    policy_match_level: str
    policy_version: str
    reply_source: str
    reply_control: dict[str, Any]
    async_final_reply: dict[str, Any]
    postprocess_changed: bool
    postprocess_reasons: list[str]
    warnings: list[dict[str, Any]]
    profile_update: dict[str, Any]
    event_updates: list[dict[str, Any]]
    saved_memory: dict[str, Any]
    memory_error: str | None

    trace: list[TraceEntry]
    errors: list[dict[str, Any]]
