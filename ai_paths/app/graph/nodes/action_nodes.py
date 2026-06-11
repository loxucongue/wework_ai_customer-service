from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_task_results import ActionToolTask, merge_action_task_results
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
    planner_tasks,
)
from app.graph.state import AgentState
from app.graph.nodes.pricing_context import pricing_sql_for_project
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.coze_client import CozeClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    pricing_repository: LocalPricingRepository | None,
    store_service: StoreService | None,
    appointment_opening_service: AppointmentOpeningService | None,
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]],
    store_query_from_state: Callable[[str, AgentState], str],
) -> Callable[[AgentState], Any]:
    async def execute_actions(state: AgentState) -> dict[str, Any]:
        tasks = planner_tasks(state)
        required_tools = planner_required_tools(state)
        with trace_logger.node(
            state,
            "execute_actions",
            {
                "primary_task": planner_primary_task(state),
                "secondary_tasks": planner_secondary_tasks(state),
                "required_tools": required_tools,
            },
        ) as span:
            content = state.get("normalized_content") or ""
            tool_results: dict[str, Any] = {}
            tool_calls: list[dict[str, Any]] = []
            tool_tasks: list[ActionToolTask] = []
            handoff = planner_handoff(state)

            for tool in required_tools:
                _queue_planned_tool_tasks(
                    tool=tool,
                    state=state,
                    coze_client=coze_client,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                    tool_tasks=tool_tasks,
                )

            if _needs_professional_assist(required_tools) or handoff.get("needed"):
                assist_result = _professional_assist_result(
                    state=state,
                    content=content,
                    tasks=tasks,
                    handoff=handoff,
                    required_tools=required_tools,
                )
                tool_results["professional_assist"] = assist_result
                tool_calls.append(
                    {
                        "name": "professional_assist",
                        "input": {
                            "planned": _needs_professional_assist(required_tools),
                            "handoff_needed": bool(handoff.get("needed")),
                        },
                        "output": assist_result,
                    }
                )

            if _needs_store_lookup(required_tools) and store_service:
                try:
                    store_query = store_query_from_state(content, state)
                    result = store_service.search(store_query, customer_context=state.get("customer_context") or {})
                    tool_results["store_lookup"] = result
                    tool_calls.append(
                        {
                            "name": "store_lookup",
                            "input": {"query": store_query, "raw_query": content},
                            "output": result,
                        }
                    )
                except Exception as exc:
                    tool_results["store_lookup"] = {"stores": [], "error": f"{type(exc).__name__}: {exc}"}
                    tool_calls.append(
                        {
                            "name": "store_lookup",
                            "input": {"query": content},
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            if _needs_appointment_record_query(required_tools):
                tool_results["appointment_record_query"] = {"handled_by_cache": True}
                tool_calls.append(
                    {
                        "name": "appointment_record_query",
                        "input": {"query": content, "planned": True},
                        "output": {"handled_by_cache": True},
                    }
                )

            if _needs_appointment_lookup(required_tools) and store_service:
                try:
                    store_query = store_query_from_state(content, state)
                    lookup = tool_results.get("store_lookup") or store_service.search(
                        store_query,
                        customer_context=state.get("customer_context") or {},
                    )
                    if "store_lookup" not in tool_results:
                        tool_results["store_lookup"] = lookup
                        tool_calls.append(
                            {
                                "name": "store_lookup",
                                "input": {"query": store_query, "raw_query": content},
                                "output": lookup,
                            }
                        )
                    appointment_query = appointment_query_from_state(content, lookup, state)
                    if _needs_available_time(required_tools):
                        if appointment_query.get("store_id") and appointment_query.get("date"):
                            available = store_service.available_time(
                                store_id=str(appointment_query["store_id"]),
                                date=str(appointment_query["date"]),
                                customer_context=state.get("customer_context") or {},
                            )
                            available["store_name"] = appointment_query.get("store_name", "")
                            available["date"] = appointment_query.get("date", "")
                            tool_results["available_time"] = available
                            tool_calls.append(
                                {
                                    "name": "available_time",
                                    "input": appointment_query,
                                    "output": available,
                                }
                            )
                        else:
                            tool_results["available_time"] = {"slots": {}, "missing": appointment_query.get("missing", [])}
                    if _needs_appointment_create(required_tools) and appointment_opening_service:
                        opening = appointment_opening_service.maybe_open(
                            content=content,
                            state=state,
                            appointment_query=appointment_query,
                            available_time=tool_results.get("available_time")
                            if isinstance(tool_results.get("available_time"), dict)
                            else {},
                        )
                        if opening.get("status") != "missing_info":
                            tool_results["appointment_opening"] = opening
                            tool_calls.append(
                                {
                                    "name": "appointment_create",
                                    "input": {
                                        "store_id": appointment_query.get("store_id"),
                                        "store_name": appointment_query.get("store_name"),
                                        "date": appointment_query.get("date"),
                                        "confirmed_by_customer": opening.get("status")
                                        not in {"needs_customer_confirmation", "missing_info"},
                                    },
                                    "output": {
                                        "status": opening.get("status"),
                                        "order_id": opening.get("order_id"),
                                        "missing": opening.get("missing"),
                                        "error": opening.get("error"),
                                    },
                                }
                            )
                except Exception as exc:
                    tool_results["available_time"] = {"slots": {}, "error": f"{type(exc).__name__}: {exc}"}
                    tool_calls.append(
                        {
                            "name": "available_time",
                            "input": {"query": content},
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            if tool_tasks:
                results = await asyncio.gather(*(task for _, _, task in tool_tasks), return_exceptions=True)
                merge_action_task_results(
                    tool_tasks=tool_tasks,
                    results=results,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                )

            if _needs_local_pricing(required_tools) and pricing_repository:
                pricing_query = _planned_tool_query(required_tools, "local_pricing")
                if pricing_query:
                    local_call = {"name": "local_pricing_xlsx", "input": {"query": pricing_query, "planned": True}}
                    try:
                        local_rows = pricing_repository.search(pricing_query)
                        tool_results["pricing_local"] = {"rows": local_rows}
                        local_call["output"] = {"rows": len(local_rows)}
                    except Exception as exc:
                        local_call["error"] = f"{type(exc).__name__}: {exc}"
                        tool_results["pricing_local"] = {"rows": [], "error": local_call["error"]}
                    tool_calls.append(local_call)
                else:
                    _record_tool_argument_error(
                        tool_results=tool_results,
                        tool_calls=tool_calls,
                        key="pricing_local",
                        error="missing_planner_query",
                        tool_input={"name": "local_pricing_xlsx", "query": "", "planned": True},
                    )

            planner_fact_output = build_planner_fact_output(tool_results, state)
            fact_envelope = dict(planner_fact_output.get("fact_envelope") or {})

            span["entry"]["tool_calls"] = tool_calls
            output = {
                "tool_results": tool_results,
                "fact_envelope": fact_envelope,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return execute_actions


def _queue_planned_tool_tasks(
    *,
    tool: dict[str, Any],
    state: AgentState,
    coze_client: CozeClient,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    tool_tasks: list[ActionToolTask],
) -> None:
    name = str(tool.get("name") or "").strip()
    if name == "no_tool":
        return
    if name == "kb_search":
        kb_name = str(tool.get("kb_name") or "").strip()
        if not kb_name:
            _record_tool_argument_error(
                tool_results=tool_results,
                tool_calls=tool_calls,
                key="kb_search",
                error="missing_planner_kb_name",
                tool_input={"planned": True, "purpose": str(tool.get("purpose") or "").strip()},
            )
            return
        query = str(tool.get("query") or "").strip()
        if not query:
            _record_tool_argument_error(
                tool_results=tool_results,
                tool_calls=tool_calls,
                key=kb_name,
                error="missing_planner_query",
                tool_input={
                    "kb_name": kb_name,
                    "query": "",
                    "planned": True,
                    "purpose": str(tool.get("purpose") or "").strip(),
                },
            )
            return
        call = {
            "name": "coze_kb_search",
            "input": {
                "kb_name": kb_name,
                "query": query,
                "planned": True,
                "purpose": str(tool.get("purpose") or "").strip(),
            },
        }
        tool_tasks.append((kb_name, call, coze_client.search_kb(kb_name, query)))
        return
    if name == "pricing_db":
        query = str(tool.get("query") or "").strip()
        if not query:
            _record_tool_argument_error(
                tool_results=tool_results,
                tool_calls=tool_calls,
                key="pricing_db",
                error="missing_planner_query",
                tool_input={
                    "name": "coze_pricing_db",
                    "query": "",
                    "planned": True,
                    "purpose": str(tool.get("purpose") or "").strip(),
                },
            )
            return
        sql = pricing_sql_for_project(query)
        tool_tasks.append(
            (
                "pricing_db",
                {"name": "coze_pricing_db", "input": {"query": query, "sql": sql, "planned": True}},
                coze_client.query_pricing_db(sql),
            )
        )
        return
    if name == "local_pricing":
        return


def _record_tool_argument_error(
    *,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    key: str,
    error: str,
    tool_input: dict[str, Any],
) -> None:
    tool_results[key] = {
        "items": [],
        "error": error,
        "missing": _missing_fields_for_error(error),
    }
    tool_calls.append(
        {
            "name": str(tool_input.get("name") or "coze_kb_search"),
            "input": tool_input,
            "error": error,
        }
    )


def _missing_fields_for_error(error: str) -> list[str]:
    if error == "missing_planner_query":
        return ["query"]
    if error == "missing_planner_kb_name":
        return ["kb_name"]
    return []


def _needs_store_lookup(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "store_lookup" for item in required_tools if isinstance(item, dict))


def _needs_appointment_record_query(required_tools: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("name") or "") == "appointment_record_query" for item in required_tools if isinstance(item, dict)
    )


def _needs_appointment_lookup(required_tools: list[dict[str, Any]]) -> bool:
    names = {str(item.get("name") or "") for item in required_tools if isinstance(item, dict)}
    return bool(names & {"available_time", "appointment_create"})


def _needs_available_time(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "available_time" for item in required_tools if isinstance(item, dict))


def _needs_appointment_create(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "appointment_create" for item in required_tools if isinstance(item, dict))


def _needs_local_pricing(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "local_pricing" for item in required_tools if isinstance(item, dict))


def _needs_professional_assist(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "professional_assist" for item in required_tools if isinstance(item, dict))


def _professional_assist_result(
    *,
    state: AgentState,
    content: str,
    tasks: list[dict[str, Any]],
    handoff: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    primary = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
    guardrail = state.get("guardrail_result") if isinstance(state.get("guardrail_result"), dict) else {}
    planned_reasons = [
        str(tool.get("purpose") or "").strip()
        for tool in required_tools
        if isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist"
    ]
    planned_reasons = [reason for reason in planned_reasons if reason]
    return {
        "status": "requested",
        "reason": str(handoff.get("reason") or primary.get("answer_goal") or primary.get("customer_need") or "").strip(),
        "task_type": str(primary.get("type") or "").strip(),
        "subtype": str(primary.get("subtype") or "").strip(),
        "policy_hint": str(primary.get("policy_hint") or "").strip(),
        "customer_message": content[:240],
        "guardrail_terms": [str(item) for item in (guardrail.get("terms") or [])[:8]],
        "planned_reasons": planned_reasons[:3],
        "required_internal_action": "professional_colleague_review",
    }


def _planned_tool_query(required_tools: list[dict[str, Any]], tool_name: str) -> str:
    for item in required_tools:
        if not isinstance(item, dict) or str(item.get("name") or "").strip() != tool_name:
            continue
        query = str(item.get("query") or "").strip()
        if query:
            return query
    return ""
