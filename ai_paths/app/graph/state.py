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
    conversation_history: list[str]
    user_id: int | None
    wechat: str | None
    external_userid: str | None
    customer_add_wechat_id: str | int | None
    confirmed_store_id: str | int | None
    confirmed_store_name: str | None
    store_id: str | int | None
    store_name: str | None
    appointment_id: str | int | None
    appointment_time: str | None
    request_context: dict[str, Any]

    normalized_content: str
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
    sales_talk_reference: dict[str, Any]

    planner_decision: str
    planner_stage: str
    planner_sub_rule_id: str
    planner_reply_messages: list[dict[str, Any]]
    planner_tool_calls: list[dict[str, Any]]
    reply_constraints: list[str]
    primary_task: dict[str, Any]
    secondary_tasks: list[dict[str, Any]]
    required_tools: list[dict[str, Any]]
    tool_policy_violations: list[dict[str, Any]]
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
    planner_source: str
    policy_id: str
    policy_family_id: str
    exact_policy_id: str
    policy_match_level: str
    policy_version: str
    reply_source: str
    postprocess_changed: bool
    postprocess_reasons: list[str]
    warnings: list[dict[str, Any]]
    profile_update: dict[str, Any]
    event_updates: list[dict[str, Any]]
    saved_memory: dict[str, Any]
    memory_error: str | None

    trace: list[TraceEntry]
    errors: list[dict[str, Any]]
