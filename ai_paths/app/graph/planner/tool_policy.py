from __future__ import annotations

from typing import Any

from app.graph.nodes.store_context import (
    should_use_known_store_context,
    should_use_recent_store_fact_context,
    store_query_from_state,
)
from app.graph.planner.planner_contract import ALLOWED_KBS, ALLOWED_TOOLS
from app.graph.state import AgentState
from app.graph.signals.project import has_case_request
from app.policies.sop_rules import normalize_sop_stage


STORE_FACT_TERMS = (
    "门店",
    "店",
    "地址",
    "位置",
    "导航",
    "路线",
    "营业时间",
    "几点开门",
    "几点关门",
    "停车",
    "机场",
    "地铁",
    "附近",
    "离我近",
    "哪家近",
    "最近",
    "我在",
    "我住",
    "我到",
)

STORE_CITY_TERMS = (
    "深圳",
    "上海",
    "厦门",
    "重庆",
    "杭州",
    "广州",
    "成都",
    "武汉",
    "长沙",
    "福州",
    "泉州",
    "南京",
    "北京",
    "西安",
    "天津",
)


def normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
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
        tools.append(tool)
    return tools


def enforce_required_tools(
    state: AgentState,
    tasks: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    original_user_query = str(state.get("normalized_content") or "").strip()[:160]
    fallback_query = _policy_tool_query(tasks) or original_user_query
    primary_task_type = str((tasks[0] if tasks else {}).get("type") or "").strip()
    sop_stage = normalize_sop_stage(
        state.get("sop_stage") or (tasks[0].get("sop_stage") if tasks else ""),
        task_type=primary_task_type,
    )

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
        if original_user_query:
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

    if needs_store_lookup_request(state, original_user_query):
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

    return dedupe_tools(tools)


def tool_policy_violations(tasks: list[dict[str, Any]], required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        if name != "kb_search":
            continue
        kb_name = str(tool.get("kb_name") or "").strip()
        query = str(tool.get("query") or "").strip()
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
                    "note": "Every kb_search must include both kb_name and a concrete query.",
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
        if task_type == "store_inquiry" and not has_tool("store_lookup"):
            missing.append("store_lookup")
        elif task_type == "case_request" and not has_tool("kb_search", kb_name="case_studies"):
            missing.append("kb_search(case_studies)")
        elif task_type == "competitor_compare" and not has_tool("kb_search", kb_name="sales_talk_qa"):
            missing.append("kb_search(sales_talk_qa)")
        elif task_type in {"appointment_status", "appointment_change", "appointment_cancel"} and not has_tool("appointment_record_query"):
            missing.append("appointment_record_query")
        elif task_type == "appointment" and not (
            has_tool("available_time") or has_tool("appointment_create") or has_tool("appointment_record_query")
        ):
            missing.append("appointment_fact_tool")
        if missing:
            violations.append(
                {
                    "task_type": task_type,
                    "subtype": str(task.get("subtype") or "").strip(),
                    "missing": ", ".join(missing),
                    "note": "Planner did not request the fact tools required by its own task type.",
                }
            )
    return violations


def dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def needs_store_lookup_request(state: AgentState, content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in STORE_FACT_TERMS) and (
        any(city in content for city in STORE_CITY_TERMS)
        or any(term in content for term in ("门店", "地址", "位置", "导航", "附近", "哪家近", "离我近", "机场", "地铁"))
    ):
        return True
    return should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state)


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
