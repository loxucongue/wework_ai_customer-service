from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.graph.planner.planner_contract import (
    ALLOWED_CONVERSION_STAGES,
    ALLOWED_CUSTOMER_TYPES,
    ALLOWED_KBS,
    ALLOWED_MAIN_BLOCKERS,
    ALLOWED_NEXT_STEPS,
    ALLOWED_TOOLS,
)
from app.graph.state import AgentState


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    decision = _normalize_decision(model_payload.get("decision") if isinstance(model_payload, dict) else "")
    stage = str(model_payload.get("stage") or "").strip() if isinstance(model_payload, dict) else ""
    sub_rule_id = str(model_payload.get("sub_rule_id") or "").strip() if isinstance(model_payload, dict) else ""
    conversion_stage = _normalize_enum(
        model_payload.get("conversion_stage") if isinstance(model_payload, dict) else "",
        ALLOWED_CONVERSION_STAGES,
        "",
    )
    customer_type = _normalize_enum(
        model_payload.get("customer_type") if isinstance(model_payload, dict) else "",
        ALLOWED_CUSTOMER_TYPES,
        "unknown",
    )
    main_blocker = _normalize_enum(
        model_payload.get("main_blocker") if isinstance(model_payload, dict) else "",
        ALLOWED_MAIN_BLOCKERS,
        "none",
    )
    next_step = _normalize_enum(
        model_payload.get("next_step") if isinstance(model_payload, dict) else "",
        ALLOWED_NEXT_STEPS,
        "no_action",
    )
    planner_reply_messages = _normalize_reply_messages(model_payload.get("reply_messages") if isinstance(model_payload, dict) else [])
    planner_tool_calls = _normalize_tools(model_payload.get("tool_calls") if isinstance(model_payload, dict) else [])
    reply_constraints = _clean_str_list(model_payload.get("reply_constraints") if isinstance(model_payload, dict) else [])
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    primary_task: dict[str, Any] = {}
    secondary_tasks: list[dict[str, Any]] = []

    reply_strategy: dict[str, Any] = {}
    required_tools = _dedupe_tools(planner_tool_calls)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    executable_tools = [tool for tool in required_tools if tool.get("name") != "no_tool"]
    if _has_store_address_message(planner_reply_messages) and not executable_tools:
        lookup_query = _store_lookup_query_from_state(state)
        planner_reply_messages = [_standard_transition_message()]
        required_tools = [
            {
                "name": "customer_store_lookup",
                "purpose": "detail",
                "query": lookup_query,
            }
        ]
        executable_tools = required_tools
        decision = "need_tools"
    if executable_tools and decision == "direct_reply":
        decision = "need_tools"
    if decision == "need_tools":
        planner_reply_messages = [_standard_transition_message()]
    handoff = _normalize_handoff(handoff_raw)
    tool_policy_violations = [
        *_rejected_tool_violations(model_payload.get("tool_calls") if isinstance(model_payload, dict) else []),
        *_tool_policy_violations(required_tools, state),
        *_payment_consistency_violations(
            decision=decision,
            conversion_stage=conversion_stage,
            next_step=next_step,
            messages=planner_reply_messages,
        ),
        *_two_text_rhythm_violations(
            state=state,
            decision=decision,
            conversion_stage=conversion_stage,
            next_step=next_step,
            messages=planner_reply_messages,
        ),
    ]
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "planner_decision": decision,
        "planner_stage": stage,
        "planner_sub_rule_id": sub_rule_id,
        "conversion_stage": conversion_stage,
        "customer_type": customer_type,
        "main_blocker": main_blocker,
        "next_step": next_step,
        "planner_reply_messages": planner_reply_messages,
        "planner_tool_calls": executable_tools,
        "reply_constraints": reply_constraints,
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": tool_policy_violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
    }


def safety_fallback_plan(state: AgentState, *, reason: str = "Planner unavailable") -> dict[str, Any]:
    handoff_reason = _fallback_handoff_reason(reason)
    return build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "HUMAN_HANDOFF_SYSTEM_UNAVAILABLE",
            "conversion_stage": "objection_resolution",
            "customer_type": "unknown",
            "main_blocker": "trust",
            "next_step": "solve_blocker",
            "reply_messages": [
                {
                    "type": "human_handoff",
                    "order": 1,
                    "content": {"handoff_reason": handoff_reason},
                }
            ],
            "tool_calls": [],
            "handoff": {"needed": True, "reason": reason or "Planner unavailable"},
        },
    )


def _fallback_handoff_reason(reason: str) -> str:
    text = " ".join(str(reason or "Planner unavailable").split())
    if text in {"Planner unavailable", ""}:
        return "模型调用失败，需要专业同事协助核对。"
    if len(text) > 180:
        text = text[:177] + "..."
    return f"模型调用失败：{text}"


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip()
    return decision if decision in {"direct_reply", "need_tools", "no_reply"} else "need_tools"


def _standard_transition_message() -> dict[str, Any]:
    return {"type": "text", "order": 1, "content": {"text": "稍等一下哈"}}


def _normalize_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _normalize_reply_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:4]:
        if not isinstance(item, dict):
            continue
        msg_type = str(item.get("type") or "text").strip()
        if msg_type not in {"text", "image", "payment_collection", "human_handoff", "store_address"}:
            msg_type = "text"
        content = item.get("content")
        if msg_type == "payment_collection":
            output.append({"type": "payment_collection", "order": len(output) + 1, "content": {"amount": 10, "remark": ""}})
            continue
        if msg_type == "store_address":
            store_id = _store_address_id(content)
            if store_id:
                output.append({"type": "store_address", "order": len(output) + 1, "content": {"store_id": store_id}})
            continue
        text = _message_text(content)
        if text:
            key = "handoff_reason" if msg_type == "human_handoff" else ("url" if msg_type == "image" else "text")
            output.append({"type": msg_type, "order": len(output) + 1, "content": {key: text}})
    return output


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "url", "handoff_reason"):
            if content.get(key):
                return str(content.get(key) or "").strip()
        return ""
    return str(content or "").strip()


def _store_address_id(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("store_id") or content.get("id") or "").strip()
    return str(content or "").strip()


def _has_store_address_message(messages: list[dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "") == "store_address" for item in messages if isinstance(item, dict))


def _store_lookup_query_from_state(state: AgentState) -> str:
    recent_store_name = _recent_store_name_from_context(state)
    if recent_store_name:
        return recent_store_name
    return str(state.get("normalized_content") or state.get("content") or "").strip()


def _recent_store_name_from_context(state: AgentState) -> str:
    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preferred_name = str(basic_info.get("preferred_store_name") or "").strip()
    if preferred_name:
        return preferred_name
    text = _state_text_context(state)
    if not text:
        return ""
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    best_name = ""
    best_pos = -1
    for store in stores:
        if not isinstance(store, dict):
            continue
        name = str(store.get("store_name") or store.get("name") or "").strip()
        if not name:
            continue
        pos = text.rfind(name)
        if pos > best_pos:
            best_name = name
            best_pos = pos
    return best_name


def _state_text_context(state: AgentState) -> str:
    chunks: list[str] = [str(state.get("normalized_content") or state.get("content") or "")]
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-8:]:
        if isinstance(item, dict):
            content = item.get("content")
            chunks.append(str(content.get("text") if isinstance(content, dict) else content or ""))
        else:
            chunks.append(str(item or ""))
    return "\n".join(chunks)


def _normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name:
            if name != "kb_search" or kb_name not in ALLOWED_KBS:
                continue
            tool["kb_name"] = kb_name
        query = str(item.get("query") or "").strip()
        if query:
            tool["query"] = query
        for key in (
            "origin",
            "candidate_store_ids",
            "candidate_source",
            "store_id",
            "date",
            "scope",
            "need_fields",
            "for_distance",
        ):
            if key in item:
                tool[key] = item[key]
        tools.append(tool)
    return tools


def _tool_policy_violations(required_tools: list[dict[str, Any]], state: AgentState) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        query = str(tool.get("query") or "").strip()
        if name == "kb_search":
            kb_name = str(tool.get("kb_name") or "").strip()
            missing_args: list[str] = []
            if not kb_name:
                missing_args.append("kb_name")
            if not query:
                missing_args.append("query")
            if missing_args:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "kb_search",
                        "missing": "kb_search_missing_query" if "query" in missing_args else "kb_search_missing_kb_name",
                        "note": "Every kb_search must include both kb_name and a concrete query; code will not invent missing search terms.",
                    }
                )
            continue
        if name == "customer_store_lookup" and str(tool.get("purpose") or "").strip() == "nearby_candidates":
            if not _location_query_has_scope_region(query, state):
                violations.append(_ambiguous_location_tool_violation("customer_store_lookup"))
            continue
        if name == "distance_calculate":
            origin = str(tool.get("origin") or tool.get("address") or tool.get("query") or "").strip()
            if not _location_query_has_scope_region(origin, state):
                violations.append(_ambiguous_location_tool_violation("distance_calculate"))

    return violations


def _ambiguous_location_tool_violation(tool_name: str) -> dict[str, str]:
    return {
        "task_type": "tool_argument",
        "subtype": tool_name,
        "missing": "location_query_missing_city_or_region",
        "note": (
            "Nearby/distance store tools require a query/origin that includes a concrete city or region from the current "
            "message, recent conversation, or customer profile. If only a nationwide ambiguous landmark is known, do not "
            "call store/distance tools; ask the customer which city or district first."
        ),
    }


def _location_query_has_scope_region(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    for token in _scope_region_tokens(state):
        compact = _compact_text(token)
        if compact and compact in text:
            return True
    return False


def _scope_region_tokens(state: AgentState) -> set[str]:
    tokens: set[str] = set()
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        for key in ("province", "city", "district"):
            raw = str(store.get(key) or "").strip()
            if not raw:
                continue
            tokens.add(raw)
            for suffix in ("省", "市", "区", "县", "旗", "自治州", "自治县", "新区"):
                if raw.endswith(suffix) and len(raw) > len(suffix):
                    tokens.add(raw[: -len(suffix)])
    return tokens


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _rejected_tool_violations(raw_tools: Any) -> list[dict[str, str]]:
    if not isinstance(raw_tools, list):
        return []
    violations: list[dict[str, str]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        if name == "kb_search" and kb_name and kb_name not in ALLOWED_KBS:
            violations.append(
                {
                    "task_type": "planner_tool_rejected",
                    "subtype": "kb_search",
                    "missing": f"unsupported_kb:{kb_name}",
                    "note": "Planner may only call kb_search(case_studies). sales_talk_qa is currently disabled.",
                }
            )
    return violations


def _payment_consistency_violations(
    *,
    decision: str,
    conversion_stage: str,
    next_step: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision == "no_reply":
        return []
    if _text_explains_previous_payment_entry(messages):
        return []
    needs_payment = conversion_stage == "deposit_push" or next_step == "send_deposit" or _text_mentions_payment_entry(messages)
    if not needs_payment or _has_payment_collection(messages):
        return []
    return [
        {
            "task_type": "reply_schema_consistency",
            "subtype": "payment_collection",
            "missing": "payment_collection_required",
            "note": (
                "When conversion_stage=deposit_push, next_step=send_deposit, or customer-facing text says an entrance/link "
                "will be sent, reply_messages must include payment_collection. If payment_collection is not appropriate, "
                "change conversion_stage/next_step/text instead of promising an entrance."
            ),
        }
    ]


def _has_payment_collection(messages: list[dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict))


def _text_mentions_payment_entry(messages: list[dict[str, Any]]) -> bool:
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    return any(term in text for term in ("发入口", "重新发", "付款入口", "收款入口", "支付入口", "现在为您发", "马上发您"))


def _text_explains_previous_payment_entry(messages: list[dict[str, Any]]) -> bool:
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    return any(term in text for term in ("刚刚发的是", "刚才发的是", "前面发的是", "之前发的是"))


def _two_text_rhythm_violations(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply" or conversion_stage == "deposit_push" or next_step == "send_deposit":
        return []
    if is_contextual_short_message(str(state.get("normalized_content") or state.get("content") or "")):
        return []
    if any(str(item.get("type") or "") != "text" for item in messages if isinstance(item, dict)):
        return []
    text_messages = [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") == "text"]
    if len(text_messages) != 1:
        return []
    text = _message_text(text_messages[0].get("content"))
    if not _looks_like_answer_with_next_step(text):
        return []
    return [
        {
            "task_type": "reply_format",
            "subtype": "two_text_rhythm",
            "missing": "two_text_required",
            "note": "This direct text reply contains both an answer and a next-step prompt. Rewrite reply_messages as two short text messages: answer first, then one light next-step action.",
        }
    ]


def _looks_like_answer_with_next_step(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 18:
        return False
    return any(
        term in value
        for term in (
            "您方便",
            "哪个区",
            "哪天",
            "今天还是明天",
            "上午还是下午",
            "周六还是周日",
            "到店看看",
            "到店看",
            "帮您看名额",
            "帮您查",
            "帮您看看",
            "我帮您看",
        )
    )


def _normalize_handoff(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    return {
        "needed": needed,
        "reason": str(raw.get("reason") or "").strip()[:180],
    }


def _normalize_memory_hint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "summary": str(raw.get("summary") or "").strip()[:180],
        "needs": _clean_str_list(raw.get("needs") or [])[:6],
        "concerns": _clean_str_list(raw.get("concerns") or [])[:6],
        "store_preference": str(raw.get("store_preference") or "").strip()[:80],
        "appointment_signals": _clean_str_list(raw.get("appointment_signals") or [])[:6],
    }


def _dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        key = (name, kb_name, query)
        if name not in ALLOWED_TOOLS or key in seen:
            continue
        seen.add(key)
        normalized = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        if query:
            normalized["query"] = query
        for extra_key in (
            "origin",
            "destination",
            "candidate_store_ids",
            "candidate_source",
            "store_id",
            "store_name",
            "date",
            "time",
            "address",
            "reason",
            "scope",
            "need_fields",
            "for_distance",
        ):
            if extra_key in item:
                normalized[extra_key] = item.get(extra_key)
        unique.append(normalized)
    return unique


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text[:180])
    return output
