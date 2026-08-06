from __future__ import annotations

from typing import Any


def reply_chain_join_shadow(
    *,
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
) -> dict[str, Any]:
    """Combine Gate and Tool Planner shadows without owning business semantics."""

    gate_route = str(gate_router_shadow.get("route_suggestion") or "").strip()
    fact_requirement = str(tool_plan_preview.get("fact_requirement") or "none").strip()
    has_read_tools = bool(tool_plan_preview.get("read_tool_calls"))
    has_unknown_tools = bool(tool_plan_preview.get("unknown_tools"))
    has_content = bool((gate_router_shadow.get("selected_content") or {}).get("message_count") or gate_router_shadow.get("direct_reply_candidate"))
    static_candidate_safe = _static_candidate_safe(gate_router_shadow)
    direct_reply_guard = _direct_reply_guard_audit(
        gate_route=gate_route,
        fact_requirement=fact_requirement,
        has_read_tools=has_read_tools,
        has_unknown_tools=has_unknown_tools,
        has_content=has_content,
        static_candidate_safe=static_candidate_safe,
    )

    final_route = _final_route(
        gate_route=gate_route,
        fact_requirement=fact_requirement,
        has_read_tools=has_read_tools,
        has_unknown_tools=has_unknown_tools,
        has_content=has_content,
        static_candidate_safe=static_candidate_safe,
    )

    direct_reply_allowed = final_route == "direct_reply"
    return _drop_empty(
        {
            "schema_version": "reply_chain_join_shadow_v1",
            "gate_route": gate_route,
            "fact_requirement": fact_requirement,
            "final_route": final_route,
            "direct_reply_allowed": direct_reply_allowed,
            "direct_reply_guard_audit": direct_reply_guard,
            "final_expression_boundary": _final_expression_boundary(
                final_route=final_route,
                direct_reply_allowed=direct_reply_allowed,
            ),
            "content_available": has_content,
            "read_tool_count": len(tool_plan_preview.get("read_tool_calls") or []),
            "deferred_write_count": len(tool_plan_preview.get("deferred_write_proposals") or []),
            "unknown_tool_count": len(tool_plan_preview.get("unknown_tools") or []),
            "join_reasons": _join_reasons(
                gate_route=gate_route,
                fact_requirement=fact_requirement,
                has_read_tools=has_read_tools,
                has_unknown_tools=has_unknown_tools,
                has_content=has_content,
                final_route=final_route,
            ),
            "source": "gate_router_and_tool_plan_shadow",
        }
    )


def _final_route(
    *,
    gate_route: str,
    fact_requirement: str,
    has_read_tools: bool,
    has_unknown_tools: bool,
    has_content: bool,
    static_candidate_safe: bool,
) -> str:
    if gate_route == "no_reply" and not has_read_tools and not has_unknown_tools:
        return "no_reply"
    if gate_route == "direct_text" and not has_content and fact_requirement == "none" and not has_read_tools and not has_unknown_tools:
        return "reply"
    if gate_route == "direct_text" and has_content and not static_candidate_safe and fact_requirement == "none" and not has_read_tools and not has_unknown_tools:
        return "reply_with_content"
    if gate_route == "direct_text" and has_content and static_candidate_safe and fact_requirement == "none" and not has_read_tools and not has_unknown_tools:
        return "direct_reply"
    if gate_route == "content_and_tools" or (has_content and (has_read_tools or has_unknown_tools)):
        return "reply_with_content_and_tools"
    if gate_route == "tools_only" or has_read_tools or has_unknown_tools:
        return "reply_with_tools"
    if gate_route in {"content_only_reply", "direct_text"} or has_content:
        return "reply_with_content"
    return "reply"


def _join_reasons(
    *,
    gate_route: str,
    fact_requirement: str,
    has_read_tools: bool,
    has_unknown_tools: bool,
    has_content: bool,
    final_route: str,
) -> list[str]:
    reasons = [f"gate_route={gate_route or 'unknown'}", f"fact_requirement={fact_requirement or 'none'}"]
    if has_content:
        reasons.append("content_candidate_available")
    if has_read_tools:
        reasons.append("read_tools_required")
    if has_unknown_tools:
        reasons.append("unknown_tools_require_review")
    if final_route == "direct_reply":
        reasons.append("direct_reply_requires_gate_direct_text_and_no_dynamic_facts")
    if gate_route == "direct_text" and not has_content:
        reasons.append("direct_text_missing_static_candidate_requires_reply")
    return reasons


def _direct_reply_guard_audit(
    *,
    gate_route: str,
    fact_requirement: str,
    has_read_tools: bool,
    has_unknown_tools: bool,
    has_content: bool,
    static_candidate_safe: bool,
) -> dict[str, Any]:
    requested = gate_route == "direct_text"
    blockers: list[str] = []
    if requested and not has_content:
        blockers.append("missing_static_gate_candidate")
    if requested and fact_requirement != "none":
        blockers.append(f"dynamic_fact_requirement:{fact_requirement or 'unknown'}")
    if requested and has_read_tools:
        blockers.append("read_tools_present")
    if requested and has_unknown_tools:
        blockers.append("unknown_tools_present")
    if requested and has_content and not static_candidate_safe:
        blockers.append("static_candidate_not_safe_for_direct_reply")
    return {
        "schema_version": "reply_chain_direct_reply_guard_audit_v1",
        "direct_reply_requested": requested,
        "static_candidate_present": has_content,
        "tool_fact_requirement_none": fact_requirement == "none",
        "read_tools_absent": not has_read_tools,
        "unknown_tools_absent": not has_unknown_tools,
        "static_candidate_safe_for_direct_reply": static_candidate_safe,
        "ready_for_direct_reply": requested and not blockers,
        "blockers": blockers,
        "source": "deterministic_join_direct_reply_guard",
    }


def _final_expression_boundary(*, final_route: str, direct_reply_allowed: bool) -> dict[str, Any]:
    reply_required = final_route not in {"direct_reply", "no_reply"}
    return {
        "schema_version": "reply_final_expression_boundary_v1",
        "reply_required_for_complex_turn": reply_required,
        "final_customer_message_owner": (
            "reply"
            if reply_required
            else ("validated_static_gate_candidate" if direct_reply_allowed else "none")
        ),
        "direct_reply_exception": direct_reply_allowed,
        "direct_reply_scope": "static_candidate_only_no_dynamic_facts" if direct_reply_allowed else "none",
        "direct_reply_requires_commit_validation": direct_reply_allowed,
        "join_generates_customer_visible_text": False,
        "join_decides_sales_psychology": False,
    }


def _static_candidate_safe(gate_router_shadow: dict[str, Any]) -> bool:
    audit = gate_router_shadow.get("direct_reply_candidate_audit")
    if not isinstance(audit, dict):
        return False
    if audit.get("schema_version") != "chat_gate_direct_reply_candidate_audit_v1":
        return False
    return audit.get("safe_for_direct_reply_static_candidate") is True


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
