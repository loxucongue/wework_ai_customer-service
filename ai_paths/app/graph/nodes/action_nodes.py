from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from app.graph.case_query_terms import build_case_query_candidates
from app.graph.customer_need_questions import customer_friendly_direction_label
from app.graph.store_anchor import current_store_anchor_from_state
from app.graph.nodes.action_callback_types import ActionCallbacks
from app.graph.nodes.action_kb_tasks import ActionToolTask, append_kb_and_pricing_tasks
from app.graph.nodes.action_module_outputs import build_active_task_output, build_handoff_output
from app.graph.nodes.project_kb_parsing import project_slices_from_tool_results
from app.graph.nodes.action_task_results import merge_action_task_results
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.appointment_schedule_service import AppointmentScheduleService
from app.services.driving_time_service import enrich_store_lookup_with_driving_times
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def _planned_tool_query(action: dict[str, Any], tool_name: str) -> str:
    planned = action.get("tool_plan")
    if not isinstance(planned, list):
        return ""
    for item in planned:
        if not isinstance(item, dict) or item.get("name") != tool_name:
            continue
        query = str(item.get("query") or "").strip()
        if query:
            return query
    return ""


def _action_uses_no_tool(action: dict[str, Any]) -> bool:
    planned = action.get("tool_plan")
    if not isinstance(planned, list):
        return False
    return any(isinstance(item, dict) and item.get("name") == "no_tool" for item in planned)


def _has_existing_appointment_id(state: AgentState) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    context_from_customer = (
        customer_context.get("request_context") if isinstance(customer_context.get("request_context"), dict) else {}
    )
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    return bool(
        str(request_context.get("appointment_id") or "").strip()
        or str(context_from_customer.get("appointment_id") or "").strip()
        or str(appointment.get("appointment_id") or "").strip()
        or str(appointment.get("order_id") or "").strip()
    )


def _prefer_contextual_store_query(content: str, planned_query: str, contextual_query: str) -> str:
    if not contextual_query:
        return planned_query
    if not planned_query:
        return contextual_query
    reference_terms = ["这家", "那家", "刚刚那家", "刚才那家", "推荐一家", "推荐一个", "帮我选", "发我", "发给我", "地址", "停车", "导航"]
    if any(term in content for term in reference_terms):
        return contextual_query
    generic_terms = ["门店地址", "地址和停车", "停车信息", "导航信息", "门店信息"]
    if any(term in planned_query for term in generic_terms) and contextual_query != planned_query:
        return contextual_query
    return planned_query


def _case_items_from_tool_results(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    value = tool_results.get("case_studies") or {}
    if isinstance(value, dict):
        items = value.get("items") or value.get("outputList") or []
        return items if isinstance(items, list) else []
    if isinstance(value, list):
        return value
    return []


def _case_need_hint_from_direction(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    mapping = [
        ("抗衰", ["抗衰", "紧致", "提升", "轮廓", "法令纹", "下颌线", "松弛", "下垂"]),
        ("毛孔", ["毛孔", "黑头", "出油", "肤质"]),
        ("补水", ["补水", "保湿", "缺水", "卡粉", "修护", "干燥"]),
        ("暗沉", ["暗沉", "提亮", "肤色", "发闷", "无光泽"]),
        ("黑色素", ["黑色素", "色沉", "淡斑", "祛斑"]),
    ]
    for hint, terms in mapping:
        if any(term in value for term in terms):
            return hint
    return ""


def _compact_case_label(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = customer_friendly_direction_label(value)
    for suffix in ["方向", "管理", "方案", "类项目", "项目", "类方向"]:
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
    value = value.replace("与屏障一起看", "").strip()
    return value


def _case_followup_queries_from_project_qa(tool_results: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for item in project_slices_from_tool_results(tool_results):
        raw_candidates = [
            str(item.get("replacement_name") or "").strip(),
            str(item.get("direction") or "").strip(),
            str(item.get("title") or "").strip().split("|")[-1].strip(),
        ]
        for raw in raw_candidates:
            if not raw:
                continue
            label = customer_friendly_direction_label(raw)
            label = re.sub(r"\s+", " ", label).strip()
            if not label:
                continue
            compact_label = _compact_case_label(label)
            need_hint = _case_need_hint_from_direction(label)
            concise_terms = [term for term in [compact_label, need_hint] if term]
            for concise in concise_terms:
                for suffix in ["案例 效果 前后对比", "案例 效果", "前后对比 案例"]:
                    normalized = f"{concise} {suffix}".strip()
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        queries.append(normalized)
                        if len(queries) >= 6:
                            return queries
            base_terms = [label, "案例", "效果", "前后对比", "改善参考"]
            for query in build_case_query_candidates(
                need_hint or label,
                base_terms=base_terms,
                face_hint=True,
            ):
                normalized = re.sub(r"\s+", " ", query).strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                queries.append(normalized)
                if len(queries) >= 6:
                    return queries
    return queries


def _appointment_store_anchor(state: AgentState) -> str:
    anchor = current_store_anchor_from_state(state)
    if anchor:
        return anchor
    appointment = state.get("appointment_cache") or {}
    if isinstance(appointment, dict):
        return str(appointment.get("store_name") or "").strip()
    return ""


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    pricing_repository: LocalPricingRepository | None,
    store_service: StoreService | None,
    appointment_opening_service: AppointmentOpeningService | None,
    appointment_schedule_service: AppointmentScheduleService | None,
    callbacks: ActionCallbacks,
) -> Callable[[AgentState], Any]:
    async def execute_actions(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "execute_actions", {"actions": state.get("action_plan", {}).get("actions", [])}) as span:
            content = state.get("normalized_content") or ""
            tool_results: dict[str, Any] = {}
            module_outputs: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            actions = state.get("action_plan", {}).get("actions", [])
            tool_tasks: list[ActionToolTask] = []

            for action in actions:
                skill = action.get("name")
                if skill == "handoff":
                    continue
                if _action_uses_no_tool(action) and skill != "appointment":
                    continue

                append_kb_and_pricing_tasks(
                    action=action,
                    state=state,
                    content=content,
                    coze_client=coze_client,
                    callbacks=callbacks,
                    tool_tasks=tool_tasks,
                )

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
                        planned_query = _planned_tool_query(action, "store_lookup")
                        contextual_query = callbacks.store_query_from_state(content, state)
                        store_query = _prefer_contextual_store_query(content, planned_query, contextual_query)
                        result = store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        result = await enrich_store_lookup_with_driving_times(
                            result,
                            query=f"{content} {store_query}",
                            coze_client=coze_client,
                        )
                        tool_results["store_lookup"] = result
                        tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": result})
                    except Exception as exc:
                        tool_results["store_lookup"] = {"stores": [], "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append({"name": "store_lookup", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})

                if skill == "appointment" and store_service:
                    try:
                        is_change_or_cancel = callbacks.has_appointment_change_or_cancel(content)
                        if callbacks.has_appointment_record_query(content) or is_change_or_cancel:
                            tool_results["appointment_record_query"] = {"handled_by_cache": True}
                            tool_calls.append({"name": "appointment_record_query", "input": {"query": content}, "output": {"handled_by_cache": True}})
                            if not is_change_or_cancel:
                                continue
                        planned_query = _planned_tool_query(action, "store_lookup")
                        contextual_query = callbacks.store_query_from_state(content, state)
                        store_query = _prefer_contextual_store_query(content, planned_query, contextual_query)
                        lookup = tool_results.get("store_lookup") or store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        if "store_lookup" not in tool_results:
                            lookup = await enrich_store_lookup_with_driving_times(
                                lookup,
                                query=f"{content} {store_query}",
                                coze_client=coze_client,
                            )
                            tool_results["store_lookup"] = lookup
                            tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": lookup})
                        appointment_query = callbacks.appointment_query_from_state(content, lookup, state)
                        if not appointment_query.get("store_id"):
                            anchor = _appointment_store_anchor(state)
                            if anchor:
                                anchored_lookup = store_service.search(
                                    anchor,
                                    customer_context=state.get("customer_context") or {},
                                )
                                anchored_lookup = await enrich_store_lookup_with_driving_times(
                                    anchored_lookup,
                                    query=anchor,
                                    coze_client=coze_client,
                                )
                                tool_results["store_lookup"] = anchored_lookup
                                tool_calls.append(
                                    {
                                        "name": "store_lookup_retry",
                                        "input": {"query": anchor, "reason": "appointment_store_anchor"},
                                        "output": anchored_lookup,
                                    }
                                )
                                appointment_query = callbacks.appointment_query_from_state(content, anchored_lookup, state)
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
                        if (
                            appointment_opening_service
                            and not callbacks.has_appointment_change_or_cancel(content)
                            and not _has_existing_appointment_id(state)
                        ):
                            opening = appointment_opening_service.maybe_open(
                                content=content,
                                state=state,
                                appointment_query=appointment_query,
                                available_time=tool_results.get("available_time") if isinstance(tool_results.get("available_time"), dict) else {},
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
                        tool_calls.append({"name": "available_time", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})
                    try:
                        if appointment_schedule_service:
                            scheduling = appointment_schedule_service.maybe_apply(
                                content=content,
                                state=state,
                                appointment_query=appointment_query,
                                available_time=tool_results.get("available_time") if isinstance(tool_results.get("available_time"), dict) else {},
                            )
                            if scheduling.get("status") != "not_applicable":
                                tool_results["appointment_action"] = scheduling
                                tool_calls.append(
                                    {
                                        "name": "appointment_schedule_action",
                                        "input": {
                                            "store_id": appointment_query.get("store_id"),
                                            "store_name": appointment_query.get("store_name"),
                                            "date": appointment_query.get("date"),
                                        },
                                        "output": {
                                            "operation": scheduling.get("operation"),
                                            "status": scheduling.get("status"),
                                            "order_id": (scheduling.get("facts") or {}).get("order_id") if isinstance(scheduling.get("facts"), dict) else "",
                                            "missing": scheduling.get("missing"),
                                            "error": scheduling.get("error"),
                                        },
                                    }
                                )
                    except Exception as exc:
                        tool_results["appointment_action"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append(
                            {
                                "name": "appointment_schedule_action",
                                "input": {
                                    "store_id": appointment_query.get("store_id"),
                                    "store_name": appointment_query.get("store_name"),
                                    "date": appointment_query.get("date"),
                                },
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

            if any(action.get("name") == "project_consult" for action in actions) and not _case_items_from_tool_results(tool_results):
                for query in _case_followup_queries_from_project_qa(tool_results):
                    call = {
                        "name": "coze_kb_search",
                        "input": {
                            "kb_name": "case_studies",
                            "query": query,
                            "planned": True,
                            "purpose": "根据项目知识库的替换词名称/方向补查同类效果案例素材",
                        },
                    }
                    try:
                        result = await coze_client.search_kb("case_studies", query)
                        callbacks.merge_kb_result(tool_results, "case_studies", result.model_dump())
                        call["output"] = {"items": len(result.items)}
                    except Exception as exc:
                        call["error"] = f"{type(exc).__name__}: {exc}"
                    tool_calls.append(call)
                    if _case_items_from_tool_results(tool_results):
                        break

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
                explicit_project = callbacks.canonical_price_project(callbacks.extract_project(content))
                price_project = callbacks.canonical_price_project(
                    explicit_project or callbacks.contextual_price_project(state)
                )
                if pricing_repository and price_project and not callbacks.is_broad_price_category(price_project):
                    pricing_query = explicit_project or callbacks.canonical_price_project(callbacks.contextual_price_project(state)) or content
                    local_call = {"name": "local_pricing_rules", "input": {"query": pricing_query}}
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
