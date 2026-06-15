from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_task_results import ActionToolTask, merge_action_task_results
from app.graph.planner.runtime_plan import (
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
)
from app.graph.state import AgentState
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.coze_client import CozeClient
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


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
                    store_query = store_query_from_state(planned_store_query or content, state)
                    result = store_service.search(store_query, customer_context=state.get("customer_context") or {})
                    tool_results["store_lookup"] = result
                    tool_calls.append(
                        {
                            "name": "store_lookup",
                            "input": {"query": store_query, "raw_query": content, "planned_query": planned_store_query},
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
                    planned_store_query = _planned_tool_query(required_tools, "store_lookup")
                    store_query = store_query_from_state(planned_store_query or content, state)
                    lookup = tool_results.get("store_lookup") or store_service.search(
                        store_query,
                        customer_context=state.get("customer_context") or {},
                    )
                    if "store_lookup" not in tool_results:
                        tool_results["store_lookup"] = lookup
                        tool_calls.append(
                            {
                                "name": "store_lookup",
                                "input": {"query": store_query, "raw_query": content, "planned_query": planned_store_query},
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


def _planned_tool_query(required_tools: list[dict[str, Any]], tool_name: str) -> str:
    for item in required_tools:
        if not isinstance(item, dict) or str(item.get("name") or "").strip() != tool_name:
            continue
        query = str(item.get("query") or "").strip()
        if query:
            return query
    return ""


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
        return {}
    origin = str(store_lookup.get("distance_origin") or store_lookup.get("area_or_landmark") or store_query or "").strip()
    if not origin:
        return {}
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
        return {}
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
