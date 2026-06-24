from __future__ import annotations

from typing import Any

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

    del state
    primary_task: dict[str, Any] = {}
    secondary_tasks: list[dict[str, Any]] = []

    reply_strategy: dict[str, Any] = {}
    required_tools = _dedupe_tools(planner_tool_calls)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    handoff = _normalize_handoff(handoff_raw)
    tool_policy_violations = [
        *_rejected_tool_violations(model_payload.get("tool_calls") if isinstance(model_payload, dict) else []),
        *_tool_policy_violations(required_tools),
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
            "decision": "no_reply",
            "stage": "S4",
            "sub_rule_id": "",
            "reply_messages": [],
            "tool_calls": [],
            "handoff": {"needed": False, "reason": "Planner unavailable"},
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


def _tool_policy_violations(required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
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
