from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_active_task_output, build_handoff_output
from app.graph.state import AgentState
from app.policies.constants import KB_BY_SKILL
from app.services.coze_client import CozeClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


@dataclass(frozen=True)
class ActionCallbacks:
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_project: Callable[[str], str]
    has_appointment_change_or_cancel: Callable[[str], bool]
    has_appointment_record_query: Callable[[str], bool]
    has_store_inquiry: Callable[[str], bool]
    is_broad_price_category: Callable[[str], bool]
    json_dumps: Callable[[Any], str]
    merge_kb_result: Callable[[dict[str, Any], str, dict[str, Any]], None]
    needs_project_price_followup: Callable[[list[dict[str, Any]], dict[str, Any], AgentState], bool]
    planned_kb_searches: Callable[[dict[str, Any], AgentState | None], list[dict[str, str]]]
    pricing_sql_from_state: Callable[[AgentState], str]
    project_price_followup_queries: Callable[[dict[str, Any]], list[str]]
    safe_query_from_state: Callable[[AgentState, Any], str]
    should_drop_planner_notes_for_skill_output: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool]
    should_suspend_active_task: Callable[[AgentState, dict[str, Any], list[dict[str, Any]]], bool]
    skill_output: Callable[[str, str, dict[str, Any], AgentState], dict[str, Any]]
    store_query_from_state: Callable[[str, AgentState], str]
    with_action_planning_notes: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    pricing_repository: LocalPricingRepository | None,
    store_service: StoreService | None,
    callbacks: ActionCallbacks,
) -> Callable[[AgentState], Any]:
    async def execute_actions(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "execute_actions", {"actions": state.get("action_plan", {}).get("actions", [])}) as span:
            content = state.get("normalized_content") or ""
            tool_results: dict[str, Any] = {}
            module_outputs: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            actions = state.get("action_plan", {}).get("actions", [])
            tool_tasks: list[tuple[str, dict[str, Any], Any]] = []

            for action in actions:
                skill = action.get("name")
                if skill == "handoff":
                    continue

                kb_name = KB_BY_SKILL.get(str(skill))
                if skill == "price_consult":
                    price_project = callbacks.canonical_price_project(
                        callbacks.contextual_price_project(state) or callbacks.extract_project(content)
                    )
                    if not price_project or callbacks.is_broad_price_category(price_project):
                        kb_name = ""
                planned_kb_searches = callbacks.planned_kb_searches(action, state)
                if planned_kb_searches:
                    for planned in planned_kb_searches:
                        planned_kb = planned.get("kb_name", "")
                        planned_query = planned.get("query", "") or callbacks.safe_query_from_state(state, skill)
                        call = {
                            "name": "coze_kb_search",
                            "input": {
                                "kb_name": planned_kb,
                                "query": planned_query,
                                "planned": True,
                                "purpose": planned.get("purpose", ""),
                            },
                        }
                        tool_tasks.append((planned_kb, call, coze_client.search_kb(planned_kb, planned_query)))
                elif kb_name:
                    query = callbacks.safe_query_from_state(state, skill)
                    call = {"name": "coze_kb_search", "input": {"kb_name": kb_name, "query": query}}
                    tool_tasks.append((kb_name, call, coze_client.search_kb(kb_name, query)))

                if skill == "price_consult":
                    price_project = callbacks.canonical_price_project(
                        callbacks.contextual_price_project(state) or callbacks.extract_project(content)
                    )
                    if price_project and not callbacks.is_broad_price_category(price_project):
                        sql = callbacks.pricing_sql_from_state(state)
                        call = {"name": "coze_pricing_db", "input": {"sql": sql}}
                        tool_tasks.append(("pricing_db", call, coze_client.query_pricing_db(sql)))

                if skill == "store" and store_service:
                    if any(item.get("intent") == "trust_issue" for item in state.get("intents", [])) and not callbacks.has_store_inquiry(content):
                        tool_results["store_lookup"] = {"stores": [], "skipped": "trust_issue_without_store_query"}
                        tool_calls.append(
                            {
                                "name": "store_lookup",
                                "input": {"query": content},
                                "output": {"skipped": "trust_issue_without_store_query"},
                            }
                        )
                        continue
                    try:
                        store_query = callbacks.store_query_from_state(content, state)
                        result = store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        tool_results["store_lookup"] = result
                        tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": result})
                    except Exception as exc:
                        tool_results["store_lookup"] = {"stores": [], "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append({"name": "store_lookup", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})

                if skill == "appointment" and store_service:
                    try:
                        if callbacks.has_appointment_record_query(content) or callbacks.has_appointment_change_or_cancel(content):
                            tool_results["appointment_record_query"] = {"handled_by_cache": True}
                            tool_calls.append({"name": "appointment_record_query", "input": {"query": content}, "output": {"handled_by_cache": True}})
                            continue
                        store_query = callbacks.store_query_from_state(content, state)
                        lookup = tool_results.get("store_lookup") or store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        if "store_lookup" not in tool_results:
                            tool_results["store_lookup"] = lookup
                            tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": lookup})
                        appointment_query = callbacks.appointment_query_from_state(content, lookup, state)
                        if appointment_query.get("store_id") and appointment_query.get("date"):
                            available = store_service.available_time(
                                store_id=str(appointment_query["store_id"]),
                                date=str(appointment_query["date"]),
                                customer_context=state.get("customer_context") or {},
                            )
                            available["store_name"] = appointment_query.get("store_name", "")
                            available["date"] = appointment_query.get("date", "")
                            tool_results["available_time"] = available
                            tool_calls.append({"name": "available_time", "input": appointment_query, "output": available})
                        else:
                            tool_results["available_time"] = {"slots": {}, "missing": appointment_query.get("missing", [])}
                    except Exception as exc:
                        tool_results["available_time"] = {"slots": {}, "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append({"name": "available_time", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})

            if tool_tasks:
                results = await asyncio.gather(*(task for _, _, task in tool_tasks), return_exceptions=True)
                for (key, call, _), result in zip(tool_tasks, results):
                    if isinstance(result, Exception):
                        call["error"] = f"{type(result).__name__}: {result}"
                        if key == "pricing_db":
                            tool_results[key] = {"rows": [], "error": call["error"]}
                        else:
                            tool_results[key] = {"kb_name": key, "items": [], "error": call["error"]}
                    elif key == "pricing_db":
                        rows = result if isinstance(result, list) else []
                        tool_results[key] = {"rows": rows[:10]}
                        call["output"] = {"rows": len(rows)}
                    else:
                        dumped = result.model_dump()
                        tool_results[key] = dumped
                        call["output"] = {"items": len(result.items)}
                    tool_calls.append(call)

            if callbacks.needs_project_price_followup(actions, tool_results, state):
                for query in callbacks.project_price_followup_queries(tool_results):
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
                        callbacks.merge_kb_result(tool_results, "project_price", result.model_dump())
                        call["output"] = {"items": len(result.items)}
                    except Exception as exc:
                        call["error"] = f"{type(exc).__name__}: {exc}"
                    tool_calls.append(call)

            if any(action.get("name") == "price_consult" for action in actions):
                db_rows = tool_results.get("pricing_db", {}).get("rows") or []
                price_project = callbacks.canonical_price_project(
                    callbacks.contextual_price_project(state) or callbacks.extract_project(content)
                )
                if not db_rows and pricing_repository and price_project and not callbacks.is_broad_price_category(price_project):
                    pricing_query = callbacks.canonical_price_project(callbacks.contextual_price_project(state)) or content
                    local_call = {"name": "local_pricing_xlsx", "input": {"query": pricing_query}}
                    try:
                        local_rows = pricing_repository.search(pricing_query)
                        tool_results["pricing_local"] = {"rows": local_rows}
                        local_call["output"] = {"rows": len(local_rows)}
                    except Exception as exc:
                        local_call["error"] = f"{type(exc).__name__}: {exc}"
                        tool_results["pricing_local"] = {"rows": [], "error": local_call["error"]}
                    tool_calls.append(local_call)

            for action in actions:
                skill = action.get("name")
                if skill == "handoff":
                    module_outputs.append(build_handoff_output(action, state))
                    continue
                skill_output = callbacks.skill_output(str(skill), content, tool_results, state)
                if callbacks.should_drop_planner_notes_for_skill_output(skill_output, action, tool_results):
                    module_outputs.append(skill_output)
                else:
                    module_outputs.append(callbacks.with_action_planning_notes(skill_output, action))

            active_task = state.get("active_task") or {}
            if (
                isinstance(active_task, dict)
                and active_task
                and not callbacks.should_suspend_active_task(state, active_task, state.get("intents", []))
            ):
                module_outputs.append(build_active_task_output(active_task, callbacks.json_dumps))

            span["entry"]["tool_calls"] = tool_calls
            output = {"tool_results": tool_results, "module_outputs": module_outputs, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return execute_actions
