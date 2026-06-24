from __future__ import annotations

import asyncio
import json
import math
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
from app.services.coze_client import CozeClient
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    store_service: StoreService | None,
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]],
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
            planned_tools = state.get("planner_tool_calls") if isinstance(state.get("planner_tool_calls"), list) else []
            if planned_tools:
                required_tools = [tool for tool in planned_tools if isinstance(tool, dict)]

            for tool in required_tools:
                _queue_planned_tool_tasks(
                    tool=tool,
                    state=state,
                    coze_client=coze_client,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                    tool_tasks=tool_tasks,
                )

            if _needs_customer_store_lookup(required_tools):
                lookup_tool = _planned_tool(required_tools, "customer_store_lookup")
                result = await _customer_store_lookup(lookup_tool, state, coze_client)
                tool_results["customer_store_lookup"] = result
                tool_calls.append({"name": "customer_store_lookup", "input": lookup_tool, "output": result})

            if _needs_distance_calculate(required_tools):
                distance_tool = _planned_tool(required_tools, "distance_calculate")
                result = await _distance_calculate(distance_tool, state, coze_client, tool_results)
                tool_results["distance_calculate"] = result
                tool_calls.append({"name": "distance_calculate", "input": distance_tool, "output": result})

            if _needs_appointment_record_query(required_tools):
                appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
                tool_results["appointment_record_query"] = {"handled_by_cache": True, **appointment}
                tool_calls.append(
                    {
                        "name": "appointment_record_query",
                        "input": {"query": content, "planned": True},
                        "output": tool_results["appointment_record_query"],
                    }
                )

            if _needs_appointment_lookup(required_tools) and store_service:
                try:
                    appointment_query = _appointment_query_from_planner(required_tools, state)
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
                except Exception as exc:
                    tool_results["available_time"] = {"slots": {}, "error": f"{type(exc).__name__}: {exc}"}
                    tool_calls.append(
                        {
                            "name": "available_time",
                            "input": {"query": content},
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            if _needs_professional_assist(required_tools):
                assist = _professional_assist_result(state)
                tool_results["professional_assist"] = assist
                tool_calls.append({"name": "professional_assist", "input": _planned_tool(required_tools, "professional_assist"), "output": assist})

            if tool_tasks:
                results = await asyncio.gather(*(task for _, _, task in tool_tasks), return_exceptions=True)
                merge_action_task_results(
                    tool_tasks=tool_tasks,
                    results=results,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                )
                _filter_case_studies_by_sent_documents(tool_results, state, tool_calls)

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
        if kb_name and kb_name != "case_studies":
            tool_results[kb_name] = {
                "kb_name": kb_name,
                "items": [],
                "error": "planner_tool_rejected",
                "rejected_reason": "Only kb_search(case_studies) is selectable. sales_talk_qa is currently disabled.",
            }
            tool_calls.append(
                {
                    "name": "planner_tool_rejected",
                    "input": {"name": "kb_search", "kb_name": kb_name, "planned": True},
                    "error": "unsupported_kb",
                }
            )
            return
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


def _filter_case_studies_by_sent_documents(
    tool_results: dict[str, Any],
    state: AgentState,
    tool_calls: list[dict[str, Any]],
) -> None:
    result = tool_results.get("case_studies")
    if not isinstance(result, dict):
        return
    items = result.get("items") if isinstance(result.get("items"), list) else []
    sent_ids = _sent_case_document_ids(state)
    raw_ids = [_document_id(item) for item in items if isinstance(item, dict)]
    visible_items = [
        item
        for item in items
        if not isinstance(item, dict) or _document_id(item) not in sent_ids
    ]
    filtered_ids = [doc_id for doc_id in raw_ids if doc_id and doc_id in sent_ids]
    result["items"] = visible_items
    result["case_studies_filter"] = {
        "raw_count": len(items),
        "filtered_count": len(filtered_ids),
        "filtered_document_ids": filtered_ids,
        "visible_document_ids": [_document_id(item) for item in visible_items if isinstance(item, dict) and _document_id(item)],
    }
    if items and not visible_items:
        result["no_visible_items_reason"] = "all_case_studies_already_sent_to_customer"
    tool_calls.append(
        {
            "name": "case_studies_document_filter",
            "input": {"sent_document_ids": sorted(sent_ids)},
            "output": result["case_studies_filter"],
        }
    )


def _sent_case_document_ids(state: AgentState) -> set[str]:
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    raw = profile.get("sent_case_document_ids") if isinstance(profile.get("sent_case_document_ids"), list) else []
    return {str(item).strip() for item in raw if str(item).strip()}


def _document_id(item: dict[str, Any]) -> str:
    return str(item.get("document_id") or item.get("documentId") or "").strip()


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


def _needs_distance_calculate(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "distance_calculate" for item in required_tools if isinstance(item, dict))


def _needs_customer_store_lookup(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "customer_store_lookup" for item in required_tools if isinstance(item, dict))


def _needs_appointment_record_query(required_tools: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("name") or "") == "appointment_record_query" for item in required_tools if isinstance(item, dict)
    )


def _needs_appointment_lookup(required_tools: list[dict[str, Any]]) -> bool:
    names = {str(item.get("name") or "") for item in required_tools if isinstance(item, dict)}
    return bool(names & {"available_time"})


def _needs_available_time(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "available_time" for item in required_tools if isinstance(item, dict))


def _needs_professional_assist(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "professional_assist" for item in required_tools if isinstance(item, dict))


def _planned_tool(required_tools: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for item in required_tools:
        if isinstance(item, dict) and str(item.get("name") or "").strip() == tool_name:
            return item
    return {"name": tool_name}


def _appointment_query_from_planner(required_tools: list[dict[str, Any]], state: AgentState) -> dict[str, Any]:
    tool = _planned_tool(required_tools, "available_time")
    store_id = str(tool.get("store_id") or state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    date = str(tool.get("date") or "").strip()
    store_name = str(tool.get("store_name") or state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    missing = []
    if not store_id:
        missing.append("store_id")
    if not date:
        missing.append("date")
    return {"store_id": store_id, "store_name": store_name, "date": date, "missing": missing}


async def _customer_store_lookup(tool: dict[str, Any], state: AgentState, coze_client: CozeClient) -> dict[str, Any]:
    query = str(tool.get("query") or tool.get("origin") or tool.get("address") or state.get("normalized_content") or "").strip()
    purpose = str(tool.get("purpose") or "").strip()
    stores = _customer_scope_stores(state)
    if not query:
        return {
            "status": "missing_query",
            "query": "",
            "purpose": purpose,
            "stores": [],
            "candidate_stores": [],
            "candidate_store_count": 0,
            "error": "missing_query",
        }

    workflow_id = str(getattr(coze_client.settings, "geocode_workflow_id", "") or "").strip()
    geocode: dict[str, Any] = {}
    if workflow_id:
        geocode = await _geocode_address(coze_client, workflow_id, query)

    candidates = _stores_for_geocode(geocode, stores, purpose)
    source = "customer_scope_geocode"
    if not candidates:
        candidates = _stores_for_text_query(query, stores, purpose)
        source = "customer_scope_text_match"

    normalized = [_store_lookup_item(store) for store in candidates[:60]]
    status = "ok" if normalized else "no_match"
    return {
        "status": status,
        "query": query,
        "purpose": purpose,
        "source": source,
        "geocode": {key: geocode.get(key) for key in ("formatted_address", "province", "city", "district", "location") if geocode.get(key)},
        "stores": normalized[:12],
        "candidate_stores": normalized,
        "candidate_store_count": len(normalized),
        "missing": [] if normalized else ["matched_customer_scope_store"],
    }


async def _distance_calculate(
    tool: dict[str, Any],
    state: AgentState,
    coze_client: CozeClient,
    tool_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    origin = str(tool.get("origin") or tool.get("address") or tool.get("query") or state.get("normalized_content") or "").strip()
    geocode_origin = _normalize_distance_origin_from_store_regions(origin, state)
    candidates = _distance_candidate_stores(tool, state, tool_results or {})
    if not origin:
        return {"status": "missing_origin", "candidate_stores": candidates, "error": "missing_origin"}
    if not candidates:
        return {"origin": origin, "status": "no_candidate_stores", "candidate_stores": [], "error": "no_candidate_stores"}
    geocode_workflow_id = str(getattr(coze_client.settings, "geocode_workflow_id", "") or "").strip()
    distance_workflow_id = str(getattr(coze_client.settings, "distance_workflow_id", "") or "").strip()
    if not geocode_workflow_id:
        return {
            "origin": origin,
            "candidate_stores": candidates,
            "status": "distance_tool_unavailable",
            "error": "geocode_workflow_id_not_configured",
        }
    try:
        admin_candidate = _administrative_area_origin_candidate(origin, state)
        origin_geo = await _geocode_address(coze_client, geocode_workflow_id, geocode_origin)
        if admin_candidate and not _geocode_matches_area(origin_geo, admin_candidate["area"]):
            admin_geo = await _geocode_address(coze_client, geocode_workflow_id, admin_candidate["origin"])
            if _geocode_matches_area(admin_geo, admin_candidate["area"]) or _geocode_has_unconflicted_location(admin_geo):
                origin_geo = admin_geo
                geocode_origin = admin_candidate["origin"]
        if not origin_geo.get("location"):
            return {"origin": origin, "candidate_stores": candidates, "status": "origin_geocode_failed", "error": "origin_geocode_failed"}
        origin_point = _parse_lng_lat(str(origin_geo.get("location") or ""))
        if not origin_point:
            return {"origin": origin, "candidate_stores": candidates, "status": "origin_geocode_failed", "error": "invalid_origin_location"}

        async def rank_store(store: dict[str, Any]) -> dict[str, Any]:
            address = str(store.get("store_address") or "").strip()
            cached_location = str(store.get("location") or "").strip()
            geo: dict[str, Any] = {}
            point = _parse_lng_lat(cached_location) if cached_location else None
            if point:
                geo = {
                    key: store.get(key)
                    for key in ("geocode_formatted_address", "province", "city", "district", "location")
                    if store.get(key)
                }
                if store.get("geocode_formatted_address"):
                    geo["formatted_address"] = store.get("geocode_formatted_address")
                geo["location"] = cached_location
            else:
                geo = await _geocode_address(coze_client, geocode_workflow_id, address)
                point = _parse_lng_lat(str(geo.get("location") or ""))
            ranked = dict(store)
            ranked["geocode"] = {key: geo.get(key) for key in ("formatted_address", "province", "city", "district", "location")}
            if point:
                destination = str(geo.get("location") or cached_location or "").strip()
                distance = await _distance_between_points(coze_client, distance_workflow_id, str(origin_geo.get("location") or ""), destination)
                if distance.get("distance_km") is not None:
                    ranked["distance_km"] = distance["distance_km"]
                    ranked["distance_meters"] = distance.get("distance_meters")
                    ranked["duration_seconds"] = distance.get("duration_seconds")
                    ranked["distance_source"] = distance.get("source")
                else:
                    ranked["distance_km"] = round(_haversine_km(origin_point, point), 2)
                    ranked["distance_source"] = "haversine_fallback"
                    ranked["distance_error"] = distance.get("error") or "distance_workflow_failed"
            else:
                ranked["distance_error"] = "store_geocode_failed"
            return ranked

        ranked = await asyncio.gather(*(rank_store(store) for store in candidates[:12]), return_exceptions=True)
        ranked_stores = [item for item in ranked if isinstance(item, dict)]
        ranked_stores.sort(key=lambda item: float(item.get("distance_km") if item.get("distance_km") is not None else 999999))
        return {
            "origin": origin,
            "geocode_origin": geocode_origin,
            "origin_geocode": {key: origin_geo.get(key) for key in ("formatted_address", "province", "city", "district", "location")},
            "distance_workflow_id": distance_workflow_id,
            "status": "ok",
            "ranked_stores": ranked_stores,
            "candidate_store_count": len(candidates),
        }
    except Exception as exc:
        return {
            "origin": origin,
            "candidate_stores": candidates[:12],
            "status": "distance_tool_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _distance_candidate_stores(tool: dict[str, Any], state: AgentState, tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    if str(tool.get("candidate_source") or "").strip() == "customer_store_lookup":
        lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results, dict) else {}
        lookup_candidates = lookup.get("candidate_stores") if isinstance(lookup, dict) and isinstance(lookup.get("candidate_stores"), list) else []
        return [_store_lookup_candidate_for_distance(item) for item in lookup_candidates[:12] if isinstance(item, dict)]
    candidate_ids = tool.get("candidate_store_ids") if isinstance(tool.get("candidate_store_ids"), list) else []
    stores = []
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    allowed_ids = {str(item) for item in candidate_ids}
    for store in knowledge.get("stores", []) if isinstance(knowledge.get("stores"), list) else []:
        if not isinstance(store, dict):
            continue
        if allowed_ids and str(store.get("store_id") or "") not in allowed_ids:
            continue
        stores.append(store)
    return stores[:12]


def _stores_for_geocode(geocode: dict[str, Any], stores: list[dict[str, Any]], purpose: str) -> list[dict[str, Any]]:
    if not isinstance(geocode, dict):
        return []
    province = str(geocode.get("province") or "").strip()
    city = str(geocode.get("city") or "").strip()
    district = str(geocode.get("district") or "").strip()
    if not any((province, city, district)):
        return []
    if purpose == "nearby_candidates" and city:
        city_matches = [store for store in stores if _region_equal(store.get("city"), city)]
        if city_matches:
            return city_matches
    district_matches = [
        store
        for store in stores
        if (not province or _region_equal(store.get("province"), province))
        and (not city or _region_equal(store.get("city"), city))
        and district
        and _region_equal(store.get("district"), district)
    ]
    if district_matches:
        return district_matches
    city_matches = [
        store
        for store in stores
        if (not province or _region_equal(store.get("province"), province))
        and city
        and _region_equal(store.get("city"), city)
    ]
    if city_matches:
        return city_matches
    if province:
        return [store for store in stores if _region_equal(store.get("province"), province)]
    return []


def _stores_for_text_query(query: str, stores: list[dict[str, Any]], purpose: str) -> list[dict[str, Any]]:
    text = _compact_text(query)
    if not text:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for store in stores:
        score = _store_text_match_score(text, store)
        if score > 0:
            scored.append((score, store))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], str(item[1].get("store_id") or "")))
    if purpose == "nearby_candidates":
        top_city = str(scored[0][1].get("city") or "").strip()
        if top_city:
            city_stores = [store for store in stores if _region_equal(store.get("city"), top_city)]
            if city_stores:
                return city_stores
    return [store for _, store in scored]


def _store_text_match_score(text: str, store: dict[str, Any]) -> int:
    score = 0
    for key, weight in (
        ("store_name", 8),
        ("city", 6),
        ("district", 5),
        ("province", 4),
        ("store_address", 3),
        ("parking_name", 2),
        ("parking_address", 2),
    ):
        value = _compact_text(store.get(key))
        if value and (value in text or text in value):
            score += weight
            continue
        for token in _region_tokens(str(store.get(key) or "")):
            compact_token = _compact_text(token)
            if compact_token and compact_token in text:
                score += weight
                break
    return score


def _store_lookup_item(store: dict[str, Any]) -> dict[str, Any]:
    parking = str(store.get("parking_name") or store.get("parking_address") or "").strip()
    return {
        "id": str(store.get("store_id") or "").strip(),
        "store_id": str(store.get("store_id") or "").strip(),
        "name": str(store.get("store_name") or "").strip(),
        "store_name": str(store.get("store_name") or "").strip(),
        "province": str(store.get("province") or "").strip(),
        "city": str(store.get("city") or "").strip(),
        "district": str(store.get("district") or "").strip(),
        "address": str(store.get("store_address") or "").strip(),
        "store_address": str(store.get("store_address") or "").strip(),
        "business_hours": str(store.get("business_hours") or "").strip(),
        "parking": parking,
        "parking_name": str(store.get("parking_name") or "").strip(),
        "parking_address": str(store.get("parking_address") or "").strip(),
        "map_url": str(store.get("map_url") or "").strip(),
        "location": str(store.get("location") or "").strip(),
    }


def _store_lookup_candidate_for_distance(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "store_id": str(item.get("store_id") or item.get("id") or "").strip(),
        "store_name": str(item.get("store_name") or item.get("name") or "").strip(),
        "province": str(item.get("province") or "").strip(),
        "city": str(item.get("city") or "").strip(),
        "district": str(item.get("district") or "").strip(),
        "store_address": str(item.get("store_address") or item.get("address") or "").strip(),
        "business_hours": str(item.get("business_hours") or "").strip(),
        "parking_name": str(item.get("parking_name") or item.get("parking") or "").strip(),
        "parking_address": str(item.get("parking_address") or "").strip(),
        "map_url": str(item.get("map_url") or "").strip(),
        "location": str(item.get("location") or "").strip(),
    }


def _region_equal(left: Any, right: Any) -> bool:
    left_tokens = {_compact_text(token) for token in _region_tokens(str(left or "")) if _compact_text(token)}
    right_tokens = {_compact_text(token) for token in _region_tokens(str(right or "")) if _compact_text(token)}
    return bool(left_tokens & right_tokens)


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _normalize_distance_origin_from_store_regions(origin: str, state: AgentState) -> str:
    text = str(origin or "").strip()
    if not text:
        return ""
    stores = _customer_scope_stores(state)
    matches: list[tuple[int, str]] = []
    for store in stores:
        province = str(store.get("province") or "").strip()
        city = str(store.get("city") or "").strip()
        district = str(store.get("district") or "").strip()
        if not city or not district:
            continue
        city_tokens = _region_tokens(city)
        district_tokens = _region_tokens(district)
        has_city = any(token and token in text for token in city_tokens)
        has_district = any(token and token in text for token in district_tokens)
        if not has_district:
            continue
        score = 2 if has_city else 1
        full_region = _join_region(province=province, city=city, district=district)
        matches.append((score, full_region))
    if not matches:
        return text
    matches.sort(key=lambda item: (-item[0], len(item[1])))
    top_score = matches[0][0]
    top_regions = sorted({region for score, region in matches if score == top_score})
    return top_regions[0] if len(top_regions) == 1 else text


def _administrative_area_origin_candidate(origin: str, state: AgentState) -> dict[str, str]:
    text = str(origin or "").strip()
    if not text:
        return {}
    stores = _customer_scope_stores(state)
    city_names = sorted({str(store.get("city") or "").strip() for store in stores if store.get("city")}, key=len, reverse=True)
    for city in city_names:
        for city_token in _region_tokens(city):
            if not city_token or city_token not in text:
                continue
            area = text.split(city_token, 1)[1]
            area = _clean_area_candidate(area)
            if not _looks_like_admin_area_candidate(area):
                continue
            return {"origin": f"{city}{area}区", "area": area}
    return {}


def _clean_area_candidate(value: str) -> str:
    text = re.sub(r"[，,。？?！!\s]", "", str(value or "").strip())
    text = re.sub(r"(附近|周边|哪家|哪个|最近|更近|比较近|近点|近一点|近|门店|店|地址|路线|导航|停车|营业时间|有|吗|呢|呀|的|在|离)", "", text)
    return text.strip()


def _looks_like_admin_area_candidate(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) < 2 or len(text) > 5:
        return False
    if text.endswith(("区", "县", "市", "镇", "街道", "机场", "车站", "火车站", "高铁站", "商场", "广场", "大厦", "医院", "学校")):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]+", text))


def _geocode_matches_area(geo: dict[str, Any], area: str) -> bool:
    text = str(area or "").strip()
    if not text or not isinstance(geo, dict):
        return False
    district = str(geo.get("district") or "").strip()
    return bool(district and text in district)


def _geocode_has_unconflicted_location(geo: dict[str, Any]) -> bool:
    if not isinstance(geo, dict):
        return False
    if not str(geo.get("location") or "").strip():
        return False
    return not str(geo.get("district") or "").strip()


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _region_tokens(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    tokens = {text}
    for suffix in ("省", "市", "区", "县", "旗", "自治州", "自治县", "新区"):
        if text.endswith(suffix) and len(text) > len(suffix):
            tokens.add(text[: -len(suffix)])
    return sorted(tokens, key=len, reverse=True)


def _join_region(*, province: str, city: str, district: str) -> str:
    parts: list[str] = []
    for value in (province, city, district):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "".join(parts)


async def _geocode_address(coze_client: CozeClient, workflow_id: str, address: str) -> dict[str, Any]:
    if not address:
        return {}
    raw = await coze_client.run_workflow(workflow_id, {"address": address})
    data = raw.get("data")
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(data, dict):
        parsed = data
    else:
        parsed = raw
    output = parsed.get("output") if isinstance(parsed, dict) else None
    if isinstance(output, list) and output and isinstance(output[0], dict):
        return output[0]
    if isinstance(output, dict):
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
        try:
            nested = json.loads(str(parsed.get("output") or ""))
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                return nested[0]
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


async def _distance_between_points(coze_client: CozeClient, workflow_id: str, origin: str, destination: str) -> dict[str, Any]:
    if not workflow_id:
        return {"source": "distance_workflow", "error": "distance_workflow_id_not_configured"}
    if not origin or not destination:
        return {"source": "distance_workflow", "error": "missing_origin_or_destination"}
    try:
        raw = await coze_client.run_workflow(workflow_id, {"origin": origin, "destination": destination})
    except Exception as exc:
        return {"source": "distance_workflow", "error": f"{type(exc).__name__}: {exc}"}
    parsed = _parse_workflow_data(raw)
    output = parsed.get("output") if isinstance(parsed, dict) else None
    if isinstance(output, str) and output:
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            output = {}
    if not isinstance(output, dict):
        output = parsed if isinstance(parsed, dict) else {}
    meters = _to_float(output.get("distance"))
    duration = _to_float(output.get("duration"))
    if meters is None:
        return {"source": "distance_workflow", "raw": output, "error": "distance_missing"}
    result: dict[str, Any] = {
        "source": "distance_workflow",
        "distance_meters": int(meters),
        "distance_km": round(meters / 1000, 2),
    }
    if duration is not None:
        result["duration_seconds"] = int(duration)
    return result


def _parse_workflow_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except json.JSONDecodeError:
            return {"output": data}
    if isinstance(data, dict):
        return data
    return raw if isinstance(raw, dict) else {}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_lng_lat(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _professional_assist_result(state: AgentState) -> dict[str, Any]:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    primary = state.get("primary_task") if isinstance(state.get("primary_task"), dict) else {}
    return {
        "status": "requested",
        "reason": str(handoff.get("reason") or primary.get("customer_need") or "").strip(),
        "task_type": str(primary.get("type") or "").strip(),
        "subtype": str(primary.get("subtype") or "").strip(),
        "policy_hint": str(primary.get("policy_hint") or "").strip(),
        "required_internal_action": "professional_colleague_follow_up",
    }
