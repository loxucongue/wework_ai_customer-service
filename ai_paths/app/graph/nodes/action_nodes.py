from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_task_results import ActionToolTask, merge_action_task_results
from app.graph.nodes.appointment_time_utils import available_time_values, filter_times_by_preference, target_time_status
from app.graph.nodes.store_context import current_real_store_from_state
from app.graph.planner.runtime_plan import (
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
)
from app.graph.state import AgentState
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.coze_client import CozeClient
from app.services import store_text
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger

MAX_REASONABLE_STORE_DISTANCE_METERS = 150_000


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    store_service: StoreService | None,
    appointment_opening_service: AppointmentOpeningService | None,
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]],
    store_query_from_state: Callable[[str, AgentState], str],
) -> Callable[[AgentState], Any]:
    async def execute_actions(state: AgentState) -> dict[str, Any]:
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

            for tool in required_tools:
                _queue_planned_tool_tasks(
                    tool=tool,
                    state=state,
                    coze_client=coze_client,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                    tool_tasks=tool_tasks,
                )

            if _needs_store_lookup(required_tools) and store_service:
                try:
                    planned_store_query = _planned_tool_query(required_tools, "store_lookup")
                    planned_distance_origin = _planned_distance_origin(required_tools)
                    store_query = _store_query_with_planned_location(
                        store_query_from_state(content, state),
                        planned_query=planned_store_query,
                        planned_distance_origin=planned_distance_origin,
                    )
                    location_geocode = await _maybe_run_location_geocode(
                        coze_client=coze_client,
                        address=planned_distance_origin or store_query,
                        raw_query=content,
                    )
                    if location_geocode:
                        tool_results["location_geocode"] = location_geocode
                        tool_calls.append(
                            {
                                "name": "location_geocode",
                                "input": location_geocode.get("input") or {},
                                "output": location_geocode,
                            }
                        )
                        store_query = _store_query_with_geocoded_location(store_query, location_geocode)
                        planned_distance_origin = _distance_origin_from_geocode(
                            location_geocode,
                            fallback=planned_distance_origin,
                        )
                    result = store_service.search(
                        store_query,
                        customer_context=state.get("customer_context") or {},
                        planner_distance_origin=planned_distance_origin,
                    )
                    tool_results["store_lookup"] = result
                    tool_calls.append(
                        {
                            "name": "store_lookup",
                            "input": {
                                "query": store_query,
                                "raw_query": content,
                                "planned_query": planned_store_query,
                                "distance_origin": planned_distance_origin,
                            },
                            "output": result,
                        }
                    )
                    distance_result = await _maybe_run_distance_lookup(
                        coze_client=coze_client,
                        store_lookup=result,
                        store_query=store_query,
                    )
                    if distance_result:
                        tool_results["distance_lookup"] = distance_result
                        _apply_distance_recommendation(tool_results.get("store_lookup"), distance_result)
                        tool_calls.append(
                            {
                                "name": "distance_lookup",
                                "input": distance_result.get("input") or {},
                                "output": distance_result,
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
                appointment_record = _appointment_record_from_state(state)
                tool_results["appointment_record_query"] = appointment_record
                tool_calls.append(
                    {
                        "name": "appointment_record_query",
                        "input": {"query": content, "planned": True},
                        "output": appointment_record,
                    }
                )

            if _needs_appointment_lookup(required_tools) and store_service:
                try:
                    planned_store_query = _planned_tool_query(required_tools, "store_lookup")
                    planned_distance_origin = _planned_distance_origin(required_tools)
                    store_query = _store_query_with_planned_location(
                        store_query_from_state(content, state),
                        planned_query=planned_store_query,
                        planned_distance_origin=planned_distance_origin,
                    )
                    location_geocode = await _maybe_run_location_geocode(
                        coze_client=coze_client,
                        address=planned_distance_origin or store_query,
                        raw_query=content,
                    )
                    if location_geocode:
                        tool_results["location_geocode"] = location_geocode
                        tool_calls.append(
                            {
                                "name": "location_geocode",
                                "input": location_geocode.get("input") or {},
                                "output": location_geocode,
                            }
                        )
                        store_query = _store_query_with_geocoded_location(store_query, location_geocode)
                        planned_distance_origin = _distance_origin_from_geocode(
                            location_geocode,
                            fallback=planned_distance_origin,
                        )
                    current_store = current_real_store_from_state(state)
                    can_use_current_store = (
                        not _needs_store_lookup(required_tools)
                        and (
                            str(current_store.get("id") or "").strip()
                            or str(current_store.get("name") or "").strip()
                        )
                    )
                    lookup = tool_results.get("store_lookup") or {}
                    if not lookup and not can_use_current_store:
                        lookup = store_service.search(
                            store_query,
                            customer_context=state.get("customer_context") or {},
                            planner_distance_origin=planned_distance_origin,
                        )
                    if lookup and "store_lookup" not in tool_results:
                        tool_results["store_lookup"] = lookup
                        tool_calls.append(
                            {
                                "name": "store_lookup",
                                "input": {
                                    "query": store_query,
                                    "raw_query": content,
                                    "planned_query": planned_store_query,
                                    "distance_origin": planned_distance_origin,
                                },
                                "output": lookup,
                            }
                        )
                    distance_result = await _maybe_run_distance_lookup(
                        coze_client=coze_client,
                        store_lookup=lookup if isinstance(lookup, dict) else {},
                        store_query=store_query,
                    )
                    if distance_result and "distance_lookup" not in tool_results:
                        tool_results["distance_lookup"] = distance_result
                        _apply_distance_recommendation(tool_results.get("store_lookup"), distance_result)
                        tool_calls.append(
                            {
                                "name": "distance_lookup",
                                "input": distance_result.get("input") or {},
                                "output": distance_result,
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
                            available["store_id"] = str(appointment_query.get("store_id") or "")
                            available["date"] = appointment_query.get("date", "")
                            available["target_time"] = appointment_query.get("time", "") or appointment_query.get("time_text", "")
                            available["time_preference"] = appointment_query.get("time_preference", "")
                            available["query"] = content
                            _enrich_available_time_result(available)
                            tool_results["available_time"] = available
                            tool_calls.append(
                                {
                                    "name": "available_time",
                                    "input": appointment_query,
                                    "output": available,
                                }
                            )
                        else:
                            tool_results["available_time"] = {
                                "slots": {},
                                "missing": appointment_query.get("missing", []),
                                "status": "missing_info",
                                "store_id": appointment_query.get("store_id", ""),
                                "store_name": appointment_query.get("store_name", ""),
                                "date": appointment_query.get("date", ""),
                                "time_preference": appointment_query.get("time_preference", ""),
                                "query": content,
                            }
                    if _needs_appointment_create(required_tools) and appointment_opening_service:
                        opening = appointment_opening_service.maybe_open(
                            content=content,
                            state=state,
                            appointment_query=appointment_query,
                            available_time=tool_results.get("available_time")
                            if isinstance(tool_results.get("available_time"), dict)
                            else {},
                        )
                        tool_results["appointment_opening"] = opening
                        tool_calls.append(
                            {
                                "name": "appointment_create",
                                "input": {
                                    "store_id": appointment_query.get("store_id"),
                                    "store_name": appointment_query.get("store_name"),
                                    "date": appointment_query.get("date"),
                                    "time": appointment_query.get("time") or appointment_query.get("time_text"),
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
                    tool_results["available_time"] = {
                        "slots": {},
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "query": content,
                    }
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

            planner_fact_output = build_planner_fact_output(tool_results, state)
            fact_envelope = dict(planner_fact_output.get("fact_envelope") or {})

            span["entry"]["tool_calls"] = tool_calls
            output = {
                "planned_tools": required_tools,
                "executed_tool_calls": tool_calls,
                "tool_results": tool_results,
                "fact_envelope": fact_envelope,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return execute_actions


def _enrich_available_time_result(available: dict[str, Any]) -> None:
    slots = available.get("slots") if isinstance(available.get("slots"), dict) else {}
    target_time = str(available.get("target_time") or "")
    query = str(available.get("query") or "")
    time_status = target_time_status(slots, target_time, query)
    all_times = available_time_values(slots)
    preferred_times = filter_times_by_preference(all_times, query)
    available_times = preferred_times or list(time_status.get("available_times") or []) or all_times
    recommended_time = ""
    if time_status.get("target_time_available") and time_status.get("target_time"):
        recommended_time = str(time_status.get("target_time") or "")
    if not recommended_time and available_times:
        recommended_time = str(available_times[0] or "")
    if not recommended_time:
        nearby = time_status.get("nearby_times") or []
        if nearby:
            recommended_time = str(nearby[0] or "")
    if available.get("error"):
        status = "error"
    elif available_times or all_times:
        status = "ok"
    else:
        status = "no_slots"
    available["available_times"] = available_times
    available["recommended_time"] = recommended_time
    available["target_time_available"] = time_status.get("target_time_available")
    available["nearby_times"] = time_status.get("nearby_times") or []
    available["status"] = status


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


def _appointment_record_from_state(state: AgentState) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    customer_id = str(customer_context.get("customer_id") or state.get("customer_id") or "").strip()
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    orders = [item for item in (customer_context.get("orders") or []) if isinstance(item, dict)]

    if appointment and (appointment.get("has_active") or appointment.get("appointment_time") or appointment.get("store_name")):
        return {
            "status": "found",
            "source": appointment.get("source") or customer_context.get("source") or "customer_context",
            "customer_id": customer_id,
            "appointment": appointment,
            "orders": orders[:3],
        }

    scheduled_orders = [
        order
        for order in orders
        if str(order.get("appointment_time") or "").strip()
        or str(order.get("store_at") or "").strip()
        or str(order.get("store_name") or "").strip()
    ]
    if scheduled_orders:
        latest = scheduled_orders[0]
        return {
            "status": "found",
            "source": customer_context.get("source") or "customer_context.orders",
            "customer_id": customer_id,
            "appointment": {
                "has_active": True,
                "status": latest.get("status") or "unknown",
                "order_id": str(latest.get("id") or ""),
                "store_id": str(latest.get("store_id") or ""),
                "store_name": str(latest.get("store_name") or ""),
                "appointment_time": str(latest.get("appointment_time") or latest.get("store_at") or ""),
                "projects": latest.get("projects") or [],
                "source": "customer_context.orders",
            },
            "orders": scheduled_orders[:3],
        }

    if not customer_id:
        return {
            "status": "missing_info",
            "missing": ["customer_id"],
            "source": customer_context.get("source") or "none",
            "appointment": {},
            "orders": [],
        }

    return {
        "status": "not_found",
        "source": customer_context.get("source") or "customer_context",
        "customer_id": customer_id,
        "appointment": {},
        "orders": orders[:3],
    }


def _needs_available_time(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "available_time" for item in required_tools if isinstance(item, dict))


def _needs_appointment_create(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "appointment_create" for item in required_tools if isinstance(item, dict))


def _planned_tool_query(required_tools: list[dict[str, Any]], tool_name: str) -> str:
    for item in required_tools:
        if not isinstance(item, dict) or str(item.get("name") or "").strip() != tool_name:
            continue
        query = str(item.get("query") or "").strip()
        if query:
            return query
    return ""


def _planned_distance_origin(required_tools: list[dict[str, Any]]) -> str:
    for item in required_tools:
        if not isinstance(item, dict) or str(item.get("name") or "").strip() != "store_lookup":
            continue
        origin = str(item.get("distance_origin") or "").strip()
        if origin:
            return origin
    return ""


def _store_query_with_planned_location(
    query: str,
    *,
    planned_query: str = "",
    planned_distance_origin: str = "",
) -> str:
    base = str(query or "").strip() or str(planned_query or "").strip()
    origin = _clean_planned_store_location(planned_distance_origin)
    if not base or not origin:
        return base or origin
    if origin in base:
        return base
    if store_text.extract_city(base, []) or store_text.extract_area_or_landmark(base):
        return base
    if not (store_text.extract_city(origin, []) or store_text.extract_area_or_landmark(origin)):
        return base
    return f"{origin} {base}".strip()


def _clean_planned_store_location(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(客户|用户|本人|我|这边|当前位置|所在位置)", "", text)
    text = re.sub(r"\s+", "", text)
    return text


async def _maybe_run_location_geocode(
    *,
    coze_client: CozeClient,
    address: str,
    raw_query: str = "",
) -> dict[str, Any]:
    workflow_id = str(getattr(coze_client.settings, "location_geocode_workflow_id", "") or "").strip()
    query = str(address or "").strip()
    if not workflow_id or not query or not _should_geocode_store_location(query, raw_query=raw_query):
        return {}
    try:
        raw = await coze_client.run_workflow(workflow_id, {"address": query})
        results = _parse_location_geocode_output(raw)
        return {
            "status": "ok" if results else "no_match",
            "source": "coze_location_geocode_workflow",
            "workflow_id": workflow_id,
            "input": {"address": query},
            "results": results,
            "best": results[0] if results else {},
            "raw": raw,
        }
    except Exception as exc:
        return {
            "status": "error",
            "source": "coze_location_geocode_workflow",
            "workflow_id": workflow_id,
            "input": {"address": query},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _parse_location_geocode_output(raw: Any) -> list[dict[str, Any]]:
    payload = _location_geocode_payload(raw)
    output = payload.get("output") if isinstance(payload, dict) else payload
    rows = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
    results: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "").strip()
        lng = lat = ""
        if "," in location:
            left, right = location.split(",", 1)
            lng, lat = left.strip(), right.strip()
        results.append(
            {
                "country": str(item.get("country") or ""),
                "province": str(item.get("province") or ""),
                "city": str(item.get("city") or ""),
                "district": str(item.get("district") or ""),
                "township": str(item.get("township") or ""),
                "street": str(item.get("street") or ""),
                "number": str(item.get("number") or ""),
                "formatted_address": str(item.get("formatted_address") or ""),
                "level": str(item.get("level") or ""),
                "location": location,
                "lng": lng,
                "lat": lat,
            }
        )
    return results


def _location_geocode_payload(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if isinstance(data, str) and data.strip():
        try:
            parsed = json.loads(data)
            return parsed
        except json.JSONDecodeError:
            return raw
    if isinstance(data, dict):
        return data
    return raw


def _store_query_with_geocoded_location(query: str, location_geocode: dict[str, Any]) -> str:
    base = str(query or "").strip()
    best = location_geocode.get("best") if isinstance(location_geocode.get("best"), dict) else {}
    if not best:
        return base
    city = _strip_city_suffix(str(best.get("city") or "").strip())
    district = str(best.get("district") or "").strip()
    formatted = str(best.get("formatted_address") or "").strip()
    parts: list[str] = []
    if city and city not in base:
        parts.append(city)
    if district and district not in base:
        parts.append(district)
    if formatted and formatted not in base:
        parts.append(formatted)
    if not formatted:
        parts.append(base)
    return " ".join(part for part in parts if part).strip()


def _should_geocode_store_location(address: str, *, raw_query: str = "") -> bool:
    text = str(address or "").strip()
    raw = str(raw_query or "").strip()
    if not text:
        return False
    query_info_area = store_text.extract_area_or_landmark(text)
    raw_area = store_text.extract_area_or_landmark(raw)
    has_city = bool(store_text.extract_city(text, []) or store_text.extract_city(raw, []))
    has_area_or_landmark = bool(query_info_area or raw_area)
    if not has_area_or_landmark:
        return False
    if has_city and not has_area_or_landmark:
        return False
    if _looks_like_generic_store_question(raw or text):
        return False
    if _looks_like_store_name_only(raw or text):
        return False
    return True


def _looks_like_generic_store_question(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return False
    generic_values = {
        "你们店在哪里",
        "你们门店在哪里",
        "店在哪里",
        "门店在哪里",
        "附近有门店吗",
        "附近有店吗",
        "附近门店",
        "附近哪家门店",
    }
    if text in generic_values:
        return True
    return bool(re.fullmatch(r"(你们)?(附近)?(有)?(门店|店)(在哪里|在哪|位置|地址|吗)?", text))


def _looks_like_store_name_only(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return False
    if any(term in text for term in ("附近", "最近", "哪家近", "有店", "有没有")):
        return False
    return "店" in text and bool(store_text.extract_city(text, []))


def _distance_origin_from_geocode(location_geocode: dict[str, Any], *, fallback: str = "") -> str:
    best = location_geocode.get("best") if isinstance(location_geocode.get("best"), dict) else {}
    if not best:
        return str(fallback or "").strip()
    location = str(best.get("location") or "").strip()
    formatted = str(best.get("formatted_address") or "").strip()
    city = str(best.get("city") or "").strip()
    district = str(best.get("district") or "").strip()
    return location or formatted or f"{city}{district}".strip() or str(fallback or "").strip()


def _strip_city_suffix(value: str) -> str:
    return re.sub(r"(市|地区|自治州|盟)$", "", str(value or "").strip())


async def _maybe_run_distance_lookup(
    *,
    coze_client: CozeClient,
    store_lookup: dict[str, Any],
    store_query: str,
) -> dict[str, Any]:
    if not isinstance(store_lookup, dict) or not store_lookup.get("distance_lookup_required"):
        return {}
    workflow_id = str(getattr(coze_client.settings, "distance_workflow_id", "") or "").strip()
    if not workflow_id:
        return {"status": "skipped", "reason": "missing_distance_workflow_id"}
    stores = [item for item in (store_lookup.get("stores") or []) if isinstance(item, dict)]
    if not stores:
        return {"status": "skipped", "reason": "no_store_candidates"}
    origin = (
        str(store_lookup.get("distance_origin") or "").strip()
        or str(store_lookup.get("area_or_landmark") or "").strip()
    )
    origin = _qualify_distance_origin(origin, store_lookup=store_lookup)
    if not origin:
        return {"status": "skipped", "reason": "missing_distance_origin"}
    candidates = [
        {
            "id": str(item.get("id") or item.get("store_id") or ""),
            "name": str(item.get("name") or ""),
            "address": str(item.get("address") or ""),
        }
        for item in stores[:5]
        if str(item.get("address") or "").strip()
    ]
    if not candidates:
        return {
            "status": "skipped",
            "reason": "no_candidate_addresses",
            "input": {"origin": origin, "query": store_query},
        }
    requests = [
        {
            "candidate": candidate,
            "payload": {
                "origin": origin,
                "destination": _distance_destination_text(candidate),
            },
        }
        for candidate in candidates
    ]
    try:
        raw_results = await asyncio.gather(
            *(coze_client.run_workflow(workflow_id, item["payload"]) for item in requests),
            return_exceptions=True,
        )
        distances = _parse_distance_workflow_output(raw_results, requests)
        distances = _drop_unreasonable_same_city_distances(distances)
        usable = [item for item in distances if str(item.get("distance_text") or "").strip()]
        raw_entries = [
            {
                "candidate": item["candidate"],
                "payload": item["payload"],
                "raw": _distance_error_payload(result)
                if isinstance(result, Exception)
                else result,
            }
            for item, result in zip(requests, raw_results, strict=False)
        ]
        if not usable:
            return {
                "status": "error",
                "source": "coze_distance_workflow",
                "workflow_id": workflow_id,
                "input": {"origin": origin, "requests": [item["payload"] for item in requests], "query": store_query},
                "distances": distances,
                "raw": raw_entries,
                "error": "distance_lookup_unavailable",
            }
        return {
            "status": "ok",
            "source": "coze_distance_workflow",
            "workflow_id": workflow_id,
            "input": {"origin": origin, "requests": [item["payload"] for item in requests], "query": store_query},
            "distances": distances,
            "raw": raw_entries,
        }
    except Exception as exc:
        return {
            "status": "error",
            "source": "coze_distance_workflow",
            "workflow_id": workflow_id,
            "input": {"origin": origin, "requests": [item["payload"] for item in requests], "query": store_query},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _parse_distance_workflow_output(
    raw_results: list[Any],
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for request_item, raw in zip(requests, raw_results, strict=False):
        candidate = request_item["candidate"]
        if isinstance(raw, Exception):
            output.append(
                {
                    **candidate,
                    "distance_text": "",
                    "distance_meters": None,
                    "duration_text": "",
                    "duration_seconds": None,
                    "status": "0",
                    "info": f"{type(raw).__name__}: {raw}",
                    "infocode": "",
                }
            )
            continue
        parsed = _distance_output_object(raw if isinstance(raw, dict) else {})
        distance_meters = _distance_meters(parsed)
        duration_seconds = _duration_seconds(parsed)
        output.append(
            {
                **candidate,
                "distance_text": _format_distance_text(parsed, distance_meters),
                "distance_meters": distance_meters,
                "duration_text": _format_duration_text(parsed, duration_seconds),
                "duration_seconds": duration_seconds,
                "status": str(parsed.get("status") or ""),
                "info": str(parsed.get("info") or ""),
                "infocode": str(parsed.get("infocode") or ""),
                "raw_output": parsed,
            }
        )
    return output


def _apply_distance_recommendation(store_lookup: Any, distance_result: dict[str, Any]) -> None:
    if not isinstance(store_lookup, dict) or not isinstance(distance_result, dict):
        return
    distances = [item for item in (distance_result.get("distances") or []) if isinstance(item, dict)]
    usable = [item for item in distances if str(item.get("distance_text") or "").strip()]
    if not usable:
        return
    ranked = sorted(
        usable,
        key=lambda item: _distance_sort_key(
            text=str(item.get("distance_text") or ""),
            meters=item.get("distance_meters"),
        ),
    )
    recommended_distance = ranked[0]
    stores = [item for item in (store_lookup.get("stores") or []) if isinstance(item, dict)]
    recommended_name = str(recommended_distance.get("name") or "").strip()
    recommended = next((item for item in stores if str(item.get("name") or "").strip() == recommended_name), {})
    if not recommended:
        recommended = {
            "id": recommended_distance.get("id"),
            "name": recommended_distance.get("name"),
            "address": recommended_distance.get("address"),
        }
    recommended = dict(recommended)
    recommended["distance_text"] = recommended_distance.get("distance_text")
    recommended["distance"] = recommended_distance.get("distance_text")
    store_lookup["recommended_store"] = recommended
    store_lookup["recommendation_reason"] = f"距离查询结果显示{recommended.get('name') or '这家门店'}更适合优先推荐。"


def _qualify_distance_origin(origin: str, *, store_lookup: dict[str, Any]) -> str:
    value = str(origin or "").strip()
    if not value:
        return ""
    if re.fullmatch(r"-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?", value):
        return value
    city = str(store_lookup.get("city") or "").strip()
    if not city or city in value:
        return value
    return f"{city}{value}"


def _drop_unreasonable_same_city_distances(distances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in distances:
        if not isinstance(item, dict):
            continue
        meters = item.get("distance_meters")
        if isinstance(meters, (int, float)) and meters > MAX_REASONABLE_STORE_DISTANCE_METERS:
            cleaned = dict(item)
            cleaned["distance_text"] = ""
            cleaned["distance_meters"] = None
            cleaned["duration_text"] = ""
            cleaned["duration_seconds"] = None
            cleaned["distance_rejected_reason"] = "distance_over_same_city_threshold"
            output.append(cleaned)
            continue
        output.append(item)
    return output


def _distance_sort_key(text: str, meters: Any = None) -> tuple[int, float, str]:
    if isinstance(meters, (int, float)) and meters >= 0:
        return (0, float(meters), (text or "").strip().lower())
    value = (text or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(km|公里|千米|m|米)", value)
    if not match:
        return (1, 999999.0, value)
    number = float(match.group(1))
    unit = match.group(2)
    meters = number * 1000 if unit in {"km", "公里", "千米"} else number
    return (0, meters, value)


def _distance_raw_text(raw: dict[str, Any]) -> str:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(raw, ensure_ascii=False, default=str)


def _json_object_from_text(text: str) -> Any:
    value = (text or "").strip()
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _distance_destination_text(candidate: dict[str, str]) -> str:
    address = str(candidate.get("address") or "").strip()
    name = str(candidate.get("name") or "").strip()
    return address or name


def _distance_error_payload(exc: Exception) -> dict[str, Any]:
    return {"error": f"{type(exc).__name__}: {exc}"}


def _distance_output_object(raw: dict[str, Any]) -> dict[str, Any]:
    payload = _distance_parse_data(raw)
    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, dict):
            return output
        if isinstance(output, str):
            parsed_output = _json_object_from_text(output)
            if isinstance(parsed_output, dict):
                return parsed_output
        return payload
    return {}


def _distance_parse_data(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, str) and data.strip():
        parsed = _json_object_from_text(data)
        if isinstance(parsed, dict):
            return parsed
    return raw


def _distance_meters(payload: dict[str, Any]) -> int | None:
    for key in ("distance", "route_distance", "distance_meters"):
        value = payload.get(key)
        meters = _int_from_value(value)
        if meters is not None:
            return meters
    return None


def _duration_seconds(payload: dict[str, Any]) -> int | None:
    for key in ("duration", "duration_seconds", "route_duration"):
        value = payload.get(key)
        seconds = _int_from_value(value)
        if seconds is not None:
            return seconds
    return None


def _int_from_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return int(float(match.group(0)))
    except ValueError:
        return None


def _format_distance_text(payload: dict[str, Any], meters: int | None) -> str:
    existing = str(payload.get("distance_text") or "").strip()
    if existing:
        return existing
    if meters is None:
        return ""
    if meters >= 1000:
        km = meters / 1000
        return f"{km:.1f}公里" if km % 1 else f"{int(km)}公里"
    return f"{meters}米"


def _format_duration_text(payload: dict[str, Any], seconds: int | None) -> str:
    existing = str(payload.get("duration_text") or "").strip()
    if existing:
        return existing
    if seconds is None:
        return ""
    minutes = max(1, round(seconds / 60))
    if minutes >= 60:
        hours = minutes // 60
        remain = minutes % 60
        return f"{hours}小时{remain}分钟" if remain else f"{hours}小时"
    return f"{minutes}分钟"
