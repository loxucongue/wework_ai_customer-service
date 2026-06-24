from __future__ import annotations

from typing import Any

from app.graph.message_cards import append_store_address_card
from app.graph.message_send_policy import should_auto_add_payment_collection, suppress_repeated_action_messages
from app.graph.message_sanitizer import normalize_store_address_card_ids, sanitize_unsupported_placeholder_text
from app.graph.planner.planner_contract import ALLOWED_KBS, ALLOWED_TOOLS
from app.graph.state import AgentState


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    decision = _normalize_decision(model_payload.get("decision") if isinstance(model_payload, dict) else "")
    stage = str(model_payload.get("stage") or "").strip() if isinstance(model_payload, dict) else ""
    sub_rule_id = str(model_payload.get("sub_rule_id") or "").strip() if isinstance(model_payload, dict) else ""
    planner_reply_messages = _normalize_reply_messages(model_payload.get("reply_messages") if isinstance(model_payload, dict) else [])
    planner_tool_calls = _normalize_tools(model_payload.get("tool_calls") if isinstance(model_payload, dict) else [])
    reply_constraints = _clean_str_list(model_payload.get("reply_constraints") if isinstance(model_payload, dict) else [])
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    decision, stage, sub_rule_id, planner_reply_messages = _repair_store_acceptance_backtracking(
        state,
        decision=decision,
        stage=stage,
        sub_rule_id=sub_rule_id,
        messages=planner_reply_messages,
    )
    primary_task = _task_from_new_contract(state, decision=decision, stage=stage, sub_rule_id=sub_rule_id)
    secondary_tasks: list[dict[str, Any]] = []
    planner_reply_messages = _ensure_payment_collection_message(
        planner_reply_messages,
        state,
        decision=decision,
        stage=stage,
        sub_rule_id=sub_rule_id,
    )
    planner_reply_messages = sanitize_unsupported_placeholder_text(planner_reply_messages, state)
    if decision == "direct_reply":
        planner_reply_messages = append_store_address_card(
            planner_reply_messages,
            {
                **state,
                "planner_stage": stage,
                "planner_sub_rule_id": sub_rule_id,
                "planner_decision": decision,
            },
        )
        planner_reply_messages = normalize_store_address_card_ids(planner_reply_messages, state)
    planner_reply_messages = suppress_repeated_action_messages(planner_reply_messages, state)

    if not primary_task:
        raise ValueError("Planner Brain missing valid primary_task")

    all_tasks = [primary_task, *secondary_tasks]
    reply_strategy = _normalize_reply_strategy({}, all_tasks)
    task_tools = [tool for task in all_tasks for tool in task.get("tools", [])]
    required_tools = _dedupe_tools([*planner_tool_calls, *task_tools])
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    required_tools = _expand_distance_candidate_tools(required_tools, state)
    handoff = _normalize_handoff(handoff_raw, primary_task, secondary_tasks, required_tools)
    tool_policy_violations = [
        *_rejected_tool_violations(model_payload.get("tool_calls") if isinstance(model_payload, dict) else []),
        *_tool_policy_violations(all_tasks, required_tools),
    ]
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "planner_decision": decision,
        "planner_stage": stage,
        "planner_sub_rule_id": sub_rule_id,
        "planner_reply_messages": planner_reply_messages,
        "planner_tool_calls": [tool for tool in required_tools if tool.get("name") != "no_tool"],
        "reply_constraints": reply_constraints,
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": tool_policy_violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
    }


def safety_fallback_plan(state: AgentState) -> dict[str, Any]:
    return build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "stage": "S4",
            "sub_rule_id": "S4_COMPLAINT_REFUND",
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "这个情况我先帮您记录清楚，让专业同事继续核对。"}}],
            "tool_calls": [{"name": "professional_assist", "purpose": "Planner was unavailable or guardrail required professional follow-up"}],
            "handoff": {"needed": True, "reason": "Planner unavailable or hard guardrail requires professional assistance"},
        },
    )


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip()
    return decision if decision in {"direct_reply", "need_tools", "no_reply"} else "need_tools"


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


def _ensure_payment_collection_message(
    messages: list[dict[str, Any]],
    state: AgentState,
    *,
    decision: str,
    stage: str,
    sub_rule_id: str,
) -> list[dict[str, Any]]:
    if decision != "direct_reply" or stage != "S3":
        return messages
    marker = " ".join(
        str(value or "")
        for value in (
            state.get("normalized_content"),
            sub_rule_id,
            _planner_message_text(messages),
        )
    )
    if not should_auto_add_payment_collection(state, marker):
        return messages
    if any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict)):
        return messages
    output = list(messages)
    output.append({"type": "payment_collection", "order": len(output) + 1, "content": {"amount": 10, "remark": ""}})
    return output


def _planner_message_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, dict):
            chunks.extend(str(content.get(key) or "") for key in ("text", "url", "handoff_reason"))
        else:
            chunks.append(str(content or ""))
    return " ".join(chunk for chunk in chunks if chunk)


def _repair_store_acceptance_backtracking(
    state: AgentState,
    *,
    decision: str,
    stage: str,
    sub_rule_id: str,
    messages: list[dict[str, Any]],
) -> tuple[str, str, str, list[dict[str, Any]]]:
    if decision != "direct_reply":
        return decision, stage, sub_rule_id, messages
    if not _short_store_acceptance(state):
        return decision, stage, sub_rule_id, messages
    store = _recent_recommended_store_from_history(state)
    if not store:
        return decision, stage, sub_rule_id, messages
    reply_text = _planner_message_text(messages)
    if not (_asks_area_again(reply_text) or (stage == "S2" and "city" in sub_rule_id.lower())):
        return decision, stage, sub_rule_id, messages
    store_name = str(store.get("store_name") or "").strip()
    text = f"可以，那我先按{store_name}给您安排。您今天还是明天方便过来？" if store_name else "可以，那我先按这家门店给您安排。您今天还是明天方便过来？"
    return (
        "direct_reply",
        "S3",
        "S3_APPOINTMENT_TIME",
        [{"type": "text", "order": 1, "content": {"text": text}}],
    )


def _short_store_acceptance(state: AgentState) -> bool:
    text = "".join(str(state.get("normalized_content") or state.get("content") or "").split())
    return text in {"可以", "行", "好", "好的", "方便", "可以的", "能到", "能去", "能过来", "没问题"}


def _asks_area_again(text: str) -> bool:
    compact = "".join(str(text or "").split())
    return any(term in compact for term in ("哪个区", "在哪个区", "什么区", "附近哪个地标", "哪个地标"))


def _recent_recommended_store_from_history(state: AgentState) -> dict[str, Any]:
    history_text = _recent_assistant_history_text(state)
    if not history_text or not any(term in history_text for term in ("方便到店", "方便过来", "方便来", "到店吗", "过来吗")):
        return {}
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        if name and name in history_text:
            return store
    return {}


def _recent_assistant_history_text(state: AgentState) -> str:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    chunks: list[str] = []
    for item in history[-4:]:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"assistant", "staff", "service", "bot"}:
                continue
            content = item.get("content")
            chunks.append(str(content.get("text") if isinstance(content, dict) else content or ""))
            continue
        raw = str(item or "")
        if raw.startswith(("助手:", "助手：", "小贝:", "小贝：", "客服:", "客服：", "AI回复:", "AI回复：")):
            chunks.append(raw)
    return "\n".join(chunks)


def _task_from_new_contract(state: AgentState, *, decision: str, stage: str, sub_rule_id: str) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    stage_scene = {
        "S1": "S1_opening_consult",
        "S2": "S2_store_address",
        "S3": "S3_price_payment",
        "S4": "S4_followup_after_sales",
    }.get(stage, "S1_opening_consult")
    task_type = {
        "S1": "project_consult",
        "S2": "store_inquiry",
        "S3": "price_inquiry",
        "S4": "after_sales",
    }.get(stage, "general_consult")
    return {
        "type": task_type,
        "subtype": sub_rule_id.lower(),
        "policy_hint": sub_rule_id,
        "scene": stage_scene,
        "subflow": decision,
        "customer_need": content[:120],
        "answer_goal": "Follow the four-stage business rules and answer with known facts only",
        "priority": 1,
        "known_info": [],
        "missing_info": [],
        "must_answer": [content[:120]] if content else [],
        "must_avoid": ["内部项目代号", "编造事实"],
        "should_ask": False,
        "tools": [{"name": "no_tool", "purpose": "Planner new contract did not request tools"}],
    }


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
        for key in ("origin", "candidate_store_ids", "store_id", "date"):
            if key in item:
                tool[key] = item[key]
        tools.append(tool)
    return tools


def _expand_distance_candidate_tools(tools: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or str(tool.get("name") or "").strip() != "distance_calculate":
            expanded.append(tool)
            continue
        normalized = dict(tool)
        candidate_ids = _string_list(normalized.get("candidate_store_ids"))
        city = _distance_origin_city(normalized, state, candidate_ids)
        city_ids = _store_ids_for_city(state, city)
        if city_ids and len(candidate_ids) < len(city_ids):
            normalized["candidate_store_ids"] = city_ids
            normalized["candidate_scope"] = "customer_store_knowledge.city"
            normalized["candidate_city"] = city
            normalized["candidate_expanded_from"] = candidate_ids
        elif candidate_ids:
            normalized["candidate_store_ids"] = candidate_ids
        expanded.append(normalized)
    return expanded


def _distance_origin_city(tool: dict[str, Any], state: AgentState, candidate_ids: list[str]) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            tool.get("origin"),
            tool.get("address"),
            tool.get("query"),
            state.get("normalized_content"),
        )
    )
    stores = _customer_scope_stores(state)
    city_names = sorted({str(store.get("city") or "").strip() for store in stores if store.get("city")}, key=len, reverse=True)
    for city in city_names:
        short_city = city[:-1] if city.endswith("市") else city
        if city and (city in text or (short_city and short_city in text)):
            return city
    if candidate_ids:
        candidate_set = set(candidate_ids)
        candidate_cities = [
            str(store.get("city") or "").strip()
            for store in stores
            if str(store.get("store_id") or "") in candidate_set and store.get("city")
        ]
        if candidate_cities:
            return candidate_cities[0]
    return ""


def _store_ids_for_city(state: AgentState, city: str) -> list[str]:
    if not city:
        return []
    ids: list[str] = []
    for store in _customer_scope_stores(state):
        if str(store.get("city") or "").strip() != city:
            continue
        store_id = str(store.get("store_id") or "").strip()
        if store_id:
            ids.append(store_id)
    return list(dict.fromkeys(ids))


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item or "").strip())]


def _tool_policy_violations(tasks: list[dict[str, Any]], required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        query = str(tool.get("query") or "").strip()
        if name != "kb_search":
            continue
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

    def has_tool(name: str, *, kb_name: str = "") -> bool:
        for tool in concrete_tools:
            if str(tool.get("name") or "").strip() != name:
                continue
            if kb_name and str(tool.get("kb_name") or "").strip() != kb_name:
                continue
            return True
        return False

    for task in tasks:
        task_type = str(task.get("type") or "").strip()
        missing: list[str] = []
        if task_type == "case_request":
            if not has_tool("kb_search", kb_name="case_studies"):
                missing.append("kb_search(case_studies)")
        elif task_type in {"appointment_status", "appointment_change", "appointment_cancel"}:
            if not has_tool("appointment_record_query"):
                missing.append("appointment_record_query")
        elif task_type == "appointment":
            if not _appointment_can_send_payment_collection(task) and not (
                has_tool("available_time") or has_tool("appointment_record_query")
            ):
                missing.append("appointment_fact_tool")

        if missing:
            violations.append(
                {
                    "task_type": task_type,
                    "subtype": str(task.get("subtype") or "").strip(),
                    "missing": ", ".join(missing),
                    "note": "Planner did not request the fact tools required by its own task type; code did not auto-add them.",
                }
            )
    return violations


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
                    "note": "Planner may only call kb_search(case_studies). sales_talk_qa is preloaded as sales_talk_reference before model input.",
                }
            )
    return violations


def _normalize_reply_strategy(raw: Any, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    primary = tasks[0] if tasks else {}
    tone = str(raw.get("tone") or "").strip() or "Natural, concise, like a real customer-service rep"
    must_answer = _clean_str_list(raw.get("must_answer") or []) or list(primary.get("must_answer") or [])
    can_push = str(raw.get("can_push") or "").strip()
    must_avoid = _clean_str_list(raw.get("must_avoid") or []) or list(primary.get("must_avoid") or [])
    try:
        max_questions = int(raw.get("max_questions", 1))
    except (TypeError, ValueError):
        max_questions = 1
    return {
        "tone": tone[:120],
        "must_answer": must_answer[:8],
        "can_push": can_push[:180],
        "must_avoid": must_avoid[:8],
        "max_questions": 0 if max_questions < 0 else min(max_questions, 2),
    }


def _appointment_can_send_payment_collection(task: dict[str, Any]) -> bool:
    text = " ".join(
        str(task.get(key) or "")
        for key in ("subtype", "policy_hint", "customer_need", "answer_goal", "must_answer")
    )
    return any(term in text for term in ("预约金", "报名", "付款入口", "收款入口", "锁名额", "交10", "10元"))


def _normalize_handoff(
    raw: Any,
    primary_task: dict[str, Any],
    secondary_tasks: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    if str(primary_task.get("type") or "").strip() in {"human_request", "complaint_refund"}:
        needed = True
    if any(str(item.get("type") or "").strip() in {"human_request", "complaint_refund"} for item in secondary_tasks):
        needed = True
    if any(str(tool.get("name") or "").strip() == "professional_assist" for tool in required_tools if isinstance(tool, dict)):
        needed = True
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
            "store_id",
            "store_name",
            "date",
            "time",
            "address",
            "reason",
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
