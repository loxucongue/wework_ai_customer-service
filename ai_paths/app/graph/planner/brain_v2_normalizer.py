from __future__ import annotations

from typing import Any

from app.graph.planner.planner_contract import ALLOWED_KBS, ALLOWED_TOOLS
from app.graph.state import AgentState
from app.graph.nodes.store_context import (
    should_use_known_store_context,
    should_use_recent_store_fact_context,
    store_query_from_state,
)
from app.graph.signals.project import has_case_request
from app.policies.sop_rules import normalize_sop_stage, normalize_sop_step


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    primary_raw = model_payload.get("primary_task") if isinstance(model_payload, dict) else {}
    secondary_raw = model_payload.get("secondary_tasks") if isinstance(model_payload, dict) else []
    reply_strategy_raw = model_payload.get("reply_strategy") if isinstance(model_payload, dict) else {}
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    primary_task = _normalize_task(primary_raw, default_priority=1)
    secondary_tasks = [
        _normalize_task(item, default_priority=index + 2)
        for index, item in enumerate(secondary_raw if isinstance(secondary_raw, list) else [])
        if isinstance(item, dict)
    ]
    secondary_tasks = [item for item in secondary_tasks if item][:2]

    if not primary_task:
        raise ValueError("Planner Brain missing valid primary_task")

    all_tasks = [primary_task, *secondary_tasks]
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    request_stage = str(request_context.get("customer_stage") or "").strip()
    sop_stage = normalize_sop_stage(
        model_payload.get("sop_stage") or primary_task.get("sop_stage"),
        task_type=str(primary_task.get("type") or ""),
        request_stage=request_stage,
    )
    sop_step = normalize_sop_step(
        sop_stage,
        model_payload.get("sop_step") or primary_task.get("sop_step") or primary_task.get("scene") or primary_task.get("subtype"),
    )
    primary_task["sop_stage"] = sop_stage
    primary_task["sop_step"] = sop_step
    reply_strategy = _normalize_reply_strategy(reply_strategy_raw, all_tasks)
    handoff = _normalize_handoff(handoff_raw, primary_task, secondary_tasks)
    required_tools = _dedupe_tools([tool for task in all_tasks for tool in task.get("tools", [])])
    required_tools = _enforce_policy_required_tools(state, all_tasks, required_tools)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    tool_policy_violations = _tool_policy_violations(all_tasks, required_tools)
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": tool_policy_violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
        "sop_stage": sop_stage,
        "sop_step": sop_step,
    }


def safety_fallback_plan(state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    primary_task = {
        "type": "human_request",
        "subtype": "planner_unavailable_or_guardrail",
        "policy_hint": "HUMAN_HANDOFF_PROFESSIONAL_ASSIST",
        "scene": "S7_dealed_active",
        "subflow": "HUMAN_HANDOFF",
        "sop_stage": "S4_FOLLOWUP_REACTIVATE",
        "sop_step": "已成交售后",
        "customer_need": content[:120] or "Needs a professional colleague to continue handling",
        "answer_goal": "Acknowledge the customer's current message and arrange professional assistance without making up facts",
        "priority": 1,
        "known_info": [],
        "missing_info": [],
        "must_answer": ["Current user question"],
        "must_avoid": ["Made-up facts", "Guaranteed results", "Code-side business judgment"],
        "should_ask": False,
        "tools": [{"name": "professional_assist", "purpose": "Planner was unavailable or guardrail required professional follow-up"}],
    }
    return build_planner_plan_v2(
        state,
        {
            "primary_task": primary_task,
            "secondary_tasks": [],
            "reply_strategy": {
                "tone": "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d",
                "must_answer": ["Current user question"],
                "can_push": "",
                "must_avoid": ["Made-up facts", "Guaranteed results", "Internal process exposure"],
                "max_questions": 0,
            },
            "handoff": {"needed": True, "reason": "Planner unavailable or hard guardrail requires professional assistance"},
            "memory_update_hint": {},
        },
    )


def planner_unavailable_fallback_plan(state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    primary_task = {
        "type": "general_consult",
        "subtype": "planner_unavailable",
        "policy_hint": "S1_OPENING_GENERAL",
        "scene": "planner_unavailable",
        "subflow": "GENERAL_DIRECT_REPLY",
        "sop_stage": "S1_GREETING_INTRO",
        "sop_step": "疑问解答",
        "customer_need": content[:120] or "Customer needs a concise answer",
        "answer_goal": "Answer the current question briefly using available facts; do not escalate unless hard guardrail is present",
        "priority": 1,
        "known_info": [],
        "missing_info": [],
        "must_answer": ["Current user question"],
        "must_avoid": ["Made-up facts", "Guaranteed results", "Professional handoff without hard guardrail"],
        "should_ask": False,
        "tools": [{"name": "kb_search", "kb_name": "sales_talk_qa", "query": content[:160], "purpose": "Need sales talk wording when planner is unavailable"}]
        if content
        else [{"name": "no_tool", "purpose": "Planner unavailable and no concrete customer content"}],
    }
    return build_planner_plan_v2(
        state,
        {
            "primary_task": primary_task,
            "secondary_tasks": [],
            "reply_strategy": {
                "tone": "Natural, concise, like a real customer-service rep",
                "must_answer": ["Current user question"],
                "can_push": "Answer first, then ask at most one next-step question if useful",
                "must_avoid": ["Made-up facts", "Guaranteed results", "Internal process exposure", "Unnecessary professional handoff"],
                "max_questions": 1,
            },
            "handoff": {"needed": False, "reason": ""},
            "memory_update_hint": {},
        },
    )


def _normalize_task(raw: Any, *, default_priority: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        priority = int(raw.get("priority", default_priority))
    except (TypeError, ValueError):
        priority = default_priority
    tools = _dedupe_tools(_normalize_tools(raw.get("tools") or []))
    return {
        "type": str(raw.get("type") or "").strip(),
        "subtype": str(raw.get("subtype") or "").strip(),
        "policy_hint": str(raw.get("policy_hint") or "").strip(),
        "scene": str(raw.get("scene") or "").strip(),
        "subflow": str(raw.get("subflow") or "").strip(),
        "sop_stage": str(raw.get("sop_stage") or "").strip(),
        "sop_step": str(raw.get("sop_step") or "").strip(),
        "customer_need": str(raw.get("customer_need") or "").strip(),
        "answer_goal": str(raw.get("answer_goal") or "").strip(),
        "priority": priority,
        "known_info": _clean_str_list(raw.get("known_info") or []),
        "missing_info": _clean_str_list(raw.get("missing_info") or []),
        "must_answer": _clean_str_list(raw.get("must_answer") or []),
        "must_avoid": _clean_str_list(raw.get("must_avoid") or []),
        "should_ask": bool(raw.get("should_ask")),
        "tools": tools or [{"name": "no_tool", "purpose": "This turn can be acknowledged directly"}],
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
        if name == "pricing_rules":
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name:
            if name != "kb_search" or kb_name not in ALLOWED_KBS:
                continue
            if kb_name == "project_qa":
                continue
            tool["kb_name"] = kb_name
        query = str(item.get("query") or "").strip()
        if query:
            tool["query"] = query
        tools.append(tool)
    return tools


def _enforce_policy_required_tools(
    state: AgentState,
    tasks: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep tool enforcement fact-only and SOP-driven.

    Planner decides the business task. Code only makes sure fact tools exist when
    a task/stage cannot be answered safely without external facts.
    """
    tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    original_user_query = str(state.get("normalized_content") or "").strip()[:160]
    fallback_query = _policy_tool_query(tasks) or original_user_query
    primary_task_type = str((tasks[0] if tasks else {}).get("type") or "").strip()
    sop_stage = normalize_sop_stage(state.get("sop_stage") or (tasks[0].get("sop_stage") if tasks else ""), task_type=primary_task_type)

    def add_tool(tool: dict[str, Any]) -> None:
        name = str(tool.get("name") or "").strip()
        kb_name = str(tool.get("kb_name") or "").strip()
        if name not in ALLOWED_TOOLS:
            return
        if name == "kb_search" and kb_name not in ALLOWED_KBS:
            return
        for existing in tools:
            if str(existing.get("name") or "").strip() != name:
                continue
            if kb_name and str(existing.get("kb_name") or "").strip() != kb_name:
                continue
            query = str(tool.get("query") or "").strip()
            purpose = str(tool.get("purpose") or "").strip()
            if query and (name == "store_lookup" or not str(existing.get("query") or "").strip()):
                existing["query"] = query
            if purpose and not str(existing.get("purpose") or "").strip():
                existing["purpose"] = purpose
            return
        normalized = {"name": name, "purpose": str(tool.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        query = str(tool.get("query") or "").strip()
        if query:
            normalized["query"] = query
        tools.append(normalized)

    def ensure_sales_talk_reference(purpose: str) -> None:
        if not original_user_query:
            return
        add_tool(
            {
                "name": "kb_search",
                "kb_name": "sales_talk_qa",
                "query": original_user_query,
                "purpose": purpose,
            }
        )

    def ensure_store_lookup(purpose: str) -> None:
        query = store_query_from_state(original_user_query or fallback_query, state)
        add_tool(
            {
                "name": "store_lookup",
                "query": query or original_user_query or fallback_query,
                "purpose": purpose,
            }
        )

    def ensure_case_studies() -> None:
        add_tool(
            {
                "name": "kb_search",
                "kb_name": "case_studies",
                "query": original_user_query or fallback_query,
                "purpose": "Need real case materials before answering effect or comparison requests",
            }
        )

    if _needs_context_store_lookup(state, original_user_query):
        ensure_store_lookup("Customer is asking for nearby or preferred store using recent city/area/landmark context")
    if has_case_request(original_user_query):
        ensure_case_studies()

    for task in tasks:
        task_type = str(task.get("type") or "").strip()
        subtype = str(task.get("subtype") or "").strip()
        policy_hint = str(task.get("policy_hint") or "").strip().upper()
        subflow = str(task.get("subflow") or "").strip().upper()
        task_stage = normalize_sop_stage(task.get("sop_stage") or sop_stage, task_type=task_type)
        markers = " ".join([task_type, subtype, policy_hint, subflow, task_stage]).upper()

        if task_stage in {"S1_GREETING_INTRO", "S3_PRICE_CLOSE", "S4_FOLLOWUP_REACTIVATE"}:
            ensure_sales_talk_reference("Need sales talk wording for the current SOP stage")
        if task_type in {"project_consult", "image_consult", "price_inquiry", "competitor_compare", "trust_issue", "after_sales"}:
            ensure_sales_talk_reference("Need sales champion wording and business-answer logic using the original customer wording")
        if task_stage == "S2_STORE_ADDRESS" or task_type == "store_inquiry":
            ensure_store_lookup("Need real store facts before answering store, address, route, hours, or parking")
        if task_type == "appointment" or any(token in markers for token in ("TIME_CHECK", "VISIT_INTENT", "CONFIRM_TIME", "WEEKEND")):
            ensure_store_lookup("Need real store facts before checking appointment availability")
            add_tool({"name": "available_time", "purpose": "Need real appointment availability before answering time or visit intent"})
        if task_type in {"appointment_status", "appointment_change", "appointment_cancel"} or any(
            token in markers for token in ("APPOINTMENT_STATUS", "APPOINTMENT_CHANGE", "APPOINTMENT_CANCEL")
        ):
            add_tool({"name": "appointment_record_query", "purpose": "Need real appointment record before status, change, or cancel handling"})
        if task_type == "case_request" or "CASE_" in markers:
            ensure_case_studies()
        if task_type in {"human_request", "complaint_refund"} or "HUMAN_HANDOFF" in markers:
            add_tool({"name": "professional_assist", "purpose": "Need professional colleague for complaint, refund, order/payment, or high-risk handling"})

    return _dedupe_tools(tools)


def _needs_context_store_lookup(state: AgentState, content: str) -> bool:
    if not content:
        return False
    if should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state):
        return True
    return False


def _policy_tool_query(tasks: list[dict[str, Any]]) -> str:
    fragments: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for key in ("customer_need", "answer_goal", "subtype", "policy_hint"):
            text = str(task.get(key) or "").strip()
            if text:
                fragments.append(text)
    return " ".join(fragments)[:160].strip()


def _tool_policy_violations(tasks: list[dict[str, Any]], required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        query = str(tool.get("query") or "").strip()
        if name == "pricing_rules" and not query:
            violations.append(
                {
                    "task_type": "tool_argument",
                    "subtype": name,
                    "missing": f"{name}_missing_query",
                    "note": f"Every {name} tool must include a concrete query; code will not infer missing search terms.",
                }
            )
            continue
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
        if task_type == "store_inquiry":
            if not has_tool("store_lookup"):
                missing.append("store_lookup")
        elif task_type == "case_request":
            if not has_tool("kb_search", kb_name="case_studies"):
                missing.append("kb_search(case_studies)")
        elif task_type == "competitor_compare":
            if not has_tool("kb_search", kb_name="sales_talk_qa"):
                missing.append("kb_search(sales_talk_qa)")
        elif task_type in {"appointment_status", "appointment_change", "appointment_cancel"}:
            if not has_tool("appointment_record_query"):
                missing.append("appointment_record_query")
        elif task_type == "appointment":
            if not (has_tool("available_time") or has_tool("appointment_create") or has_tool("appointment_record_query")):
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


def _normalize_reply_strategy(raw: Any, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    primary = tasks[0] if tasks else {}
    tone = str(raw.get("tone") or "").strip() or "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d"
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


def _normalize_handoff(raw: Any, primary_task: dict[str, Any], secondary_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    if str(primary_task.get("type") or "").strip() in {"human_request", "complaint_refund"}:
        needed = True
    if any(str(item.get("type") or "").strip() in {"human_request", "complaint_refund"} for item in secondary_tasks):
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
