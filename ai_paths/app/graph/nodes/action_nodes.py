from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_task_results import ActionToolTask, merge_action_task_results
from app.graph.nodes.tool_results import merge_kb_result
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
    planner_tasks,
)
from app.graph.state import AgentState
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
    canonical_price_project: Callable[[str], str],
    contextual_price_project: Callable[[AgentState], str],
    extract_project: Callable[[str], str],
    has_appointment_change_or_cancel: Callable[[str], bool],
    has_appointment_record_query: Callable[[str], bool],
    has_store_inquiry: Callable[[str], bool],
    is_broad_price_category: Callable[[str], bool],
    needs_project_price_followup: Callable[[list[dict[str, Any]], dict[str, Any], AgentState], bool],
    pricing_sql_from_state: Callable[[AgentState], str],
    project_price_followup_queries: Callable[[dict[str, Any]], list[str]],
    safe_query_from_state: Callable[[AgentState, str, str], str],
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
                    tool_tasks=tool_tasks,
                    pricing_sql_from_state=pricing_sql_from_state,
                    safe_query_from_state=safe_query_from_state,
                )

            if _needs_store_lookup(required_tools) and store_service:
                if _has_task_type(tasks, "trust_issue") and not has_store_inquiry(content):
                    tool_results["store_lookup"] = {"stores": [], "skipped": "trust_issue_without_store_query"}
                    tool_calls.append(
                        {
                            "name": "store_lookup",
                            "input": {"query": content},
                            "output": {"skipped": "trust_issue_without_store_query"},
                        }
                    )
                else:
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

            if _needs_appointment_tools(required_tools) and store_service:
                try:
                    if has_appointment_record_query(content) or has_appointment_change_or_cancel(content):
                        tool_results["appointment_record_query"] = {"handled_by_cache": True}
                        tool_calls.append(
                            {
                                "name": "appointment_record_query",
                                "input": {"query": content},
                                "output": {"handled_by_cache": True},
                            }
                        )
                    else:
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

            if needs_project_price_followup(tasks, tool_results, state):
                for query in project_price_followup_queries(tool_results):
                    call = {
                        "name": "coze_kb_search",
                        "input": {
                            "kb_name": "project_price",
                            "query": query,
                            "planned": True,
                            "purpose": "根据项目知识库候选方向补查价格",
                        },
                    }
                    try:
                        result = await coze_client.search_kb("project_price", query)
                        merge_kb_result(tool_results, "project_price", result.model_dump())
                        call["output"] = {"items": len(result.items)}
                    except Exception as exc:
                        call["error"] = f"{type(exc).__name__}: {exc}"
                    tool_calls.append(call)

            if _has_task_type(tasks, "price_inquiry"):
                db_rows = tool_results.get("pricing_db", {}).get("rows") or []
                price_project = canonical_price_project(contextual_price_project(state) or extract_project(content))
                if not db_rows and pricing_repository and price_project and not is_broad_price_category(price_project):
                    pricing_query = canonical_price_project(contextual_price_project(state)) or content
                    local_call = {"name": "local_pricing_xlsx", "input": {"query": pricing_query}}
                    try:
                        local_rows = pricing_repository.search(pricing_query)
                        tool_results["pricing_local"] = {"rows": local_rows}
                        local_call["output"] = {"rows": len(local_rows)}
                    except Exception as exc:
                        local_call["error"] = f"{type(exc).__name__}: {exc}"
                        tool_results["pricing_local"] = {"rows": [], "error": local_call["error"]}
                    tool_calls.append(local_call)

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
    tool_tasks: list[ActionToolTask],
    pricing_sql_from_state: Callable[[AgentState], str],
    safe_query_from_state: Callable[[AgentState, str, str], str],
) -> None:
    name = str(tool.get("name") or "").strip()
    if name == "no_tool":
        return
    if name == "kb_search":
        kb_name = str(tool.get("kb_name") or "").strip()
        if not kb_name:
            return
        query = str(tool.get("query") or "").strip()
        if not query:
            task_type, task_subtype = _default_task_for_kb(kb_name)
            query = safe_query_from_state(state, task_type, task_subtype)
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
        sql = pricing_sql_from_state(state)
        if not sql:
            return
        tool_tasks.append(("pricing_db", {"name": "coze_pricing_db", "input": {"sql": sql}}, coze_client.query_pricing_db(sql)))
        return
    if name == "local_pricing":
        return


def _needs_store_lookup(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "store_lookup" for item in required_tools if isinstance(item, dict))


def _needs_appointment_tools(required_tools: list[dict[str, Any]]) -> bool:
    names = {str(item.get("name") or "") for item in required_tools if isinstance(item, dict)}
    return bool(names & {"available_time", "appointment_record_query", "appointment_create"})


def _needs_available_time(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "available_time" for item in required_tools if isinstance(item, dict))


def _needs_appointment_create(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "appointment_create" for item in required_tools if isinstance(item, dict))


def _default_task_for_kb(kb_name: str) -> tuple[str, str]:
    return {
        "project_qa": ("project_inquiry", "project_direction"),
        "project_price": ("price_inquiry", "activity_price"),
        "sales_talk_qa": ("general_consult", "sales_talk"),
        "case_studies": ("case_request", "case_reference"),
        "competitor_qa": ("competitor_compare", "competitor_price"),
        "after_sales_qa": ("after_sales", "after_sales_support"),
    }.get(kb_name, ("general_consult", "open_consult"))


def _has_task_type(tasks: list[dict[str, Any]], expected: str) -> bool:
    expected_text = str(expected or "").strip().lower()
    if not expected_text:
        return False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        values = (
            str(task.get("type") or "").strip().lower(),
            str(task.get("subtype") or "").strip().lower(),
        )
        if expected_text in values:
            return True
    return False

