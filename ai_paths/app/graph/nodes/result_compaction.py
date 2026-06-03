from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class CompactionCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    is_broad_price_category: Callable[[str], bool]


def compact_module_outputs_for_model(outputs: list[Any]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for output in outputs[:5]:
        if not isinstance(output, dict):
            continue
        compacted.append(
            {
                "skill": output.get("skill"),
                "intent": output.get("intent"),
                "facts": [str(item)[:160] for item in (output.get("facts") or [])[:4]],
                "reply_points": [str(item)[:180] for item in (output.get("reply_points") or [])[:3]],
                "missing_slots": [str(item)[:80] for item in (output.get("missing_slots") or [])[:4]],
                "risk_flags": [str(item)[:80] for item in (output.get("risk_flags") or [])[:4]],
                "suggested_next_step": output.get("suggested_next_step"),
            }
        )
    return compacted


def store_result_wants_parking(store_lookup: dict[str, Any], state: AgentState | None = None) -> bool:
    content = (state or {}).get("normalized_content", "") if state else ""
    return bool(store_lookup.get("wants_parking")) or "停车" in str(content)


def store_result_wants_route(store_lookup: dict[str, Any], state: AgentState | None = None) -> bool:
    content = str((state or {}).get("normalized_content", "")) if state else ""
    return bool(store_lookup.get("wants_route")) or any(
        term in content for term in ["地址", "导航", "哪里", "怎么过去", "位置", "发给我", "发我", "发一下"]
    )


def store_result_wants_status(store_lookup: dict[str, Any], state: AgentState | None = None) -> bool:
    content = str((state or {}).get("normalized_content", "")) if state else ""
    return bool(store_lookup.get("wants_status")) or any(
        term in content
        for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业时间", "几点开", "几点关"]
    )


def ad_price_without_explicit_project(state: AgentState | None, project: str) -> bool:
    content = str((state or {}).get("normalized_content") or "")
    if not any(term in content for term in ["广告", "直播", "券", "团购", "小红书", "抖音"]):
        return False
    if not any(term in content for term in ["价格", "多少钱", "收费", "预约金", "尾款", "另收费", "199", "一百九十九"]):
        return False
    return not project or project in {"项目价格", "祛斑", "淡斑", "斑", "色沉", "肤色改善"}


def compact_tool_results_for_model(
    tool_results: dict[str, Any],
    state: AgentState | None = None,
    *,
    callbacks: CompactionCallbacks,
) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state or {})) if state else ""
    for key, value in tool_results.items():
        if key == "pricing_db":
            compacted[key] = {
                "rows": [] if callbacks.is_broad_price_category(project) else value.get("rows", [])[:3],
                "error": value.get("error"),
            }
            continue
        if key == "pricing_local":
            compacted[key] = {
                "rows": [] if callbacks.is_broad_price_category(project) else value.get("rows", [])[:3],
                "error": value.get("error"),
            }
            continue
        if key == "store_lookup" and isinstance(value, dict):
            recommended = value.get("recommended_store") if isinstance(value.get("recommended_store"), dict) else {}
            compacted[key] = {
                "city": value.get("city"),
                "requested_store": value.get("requested_store"),
                "location_preference": value.get("location_preference"),
                "recommendation_reason": value.get("recommendation_reason"),
                "recommended_store": {
                    "id": recommended.get("id"),
                    "name": recommended.get("name"),
                    "address": recommended.get("address"),
                    "map_url": recommended.get("map_url") if store_result_wants_route(value, state) else "",
                    "parking_name": recommended.get("parking_name") if store_result_wants_parking(value, state) else "",
                    "parking_address": recommended.get("parking_address") if store_result_wants_parking(value, state) else "",
                    "business_hours": recommended.get("business_hours") if store_result_wants_status(value, state) else "",
                }
                if recommended
                else {},
                "wants_parking": value.get("wants_parking"),
                "wants_route": value.get("wants_route"),
                "stores": [
                    {
                        "id": store.get("id"),
                        "name": store.get("name"),
                        "address": store.get("address"),
                        "map_url": store.get("map_url") if store_result_wants_route(value, state) else "",
                        "parking_name": store.get("parking_name") if store_result_wants_parking(value, state) else "",
                        "parking_address": store.get("parking_address") if store_result_wants_parking(value, state) else "",
                        "business_hours": store.get("business_hours") if store_result_wants_status(value, state) else "",
                    }
                    for store in (value.get("stores") or [])[:3]
                    if isinstance(store, dict)
                ],
                "missing": value.get("missing"),
                "error": value.get("error"),
            }
            continue
        if key == "available_time" and isinstance(value, dict):
            compacted[key] = {
                "store_name": value.get("store_name"),
                "store_id": value.get("store_id"),
                "date": value.get("date"),
                "slots": value.get("slots"),
                "missing": value.get("missing"),
                "error": value.get("error"),
            }
            continue
        if key == "project_price" and (callbacks.is_broad_price_category(project) or ad_price_without_explicit_project(state, project)):
            compacted[key] = {
                "items": [],
                "note": "客户当前没有提供明确项目名或广告截图，已隐藏具体商品项，避免拿不相关商品价代替报价。",
                "error": value.get("error") if isinstance(value, dict) else None,
            }
            continue
        items = value.get("items", []) if isinstance(value, dict) else []
        compacted[key] = {
            "items": [
                {
                    "content": str(item.get("content", ""))[:800],
                    "document_id": item.get("document_id", ""),
                }
                for item in items[:3]
                if isinstance(item, dict)
            ],
            "error": value.get("error") if isinstance(value, dict) else None,
        }
    return compacted
