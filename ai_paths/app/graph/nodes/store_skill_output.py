from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StoreSkillCallbacks:
    extract_city: Callable[[str], str]
    parking_text: Callable[[dict[str, Any]], str]


def store_skill_output(content: str, tool_results: dict[str, Any], callbacks: StoreSkillCallbacks) -> dict[str, Any]:
    """Build factual store-skill output for the final reply model."""

    lookup = tool_results.get("store_lookup", {}) if isinstance(tool_results, dict) else {}
    if not isinstance(lookup, dict):
        lookup = {}
    stores = lookup.get("stores", [])
    if not isinstance(stores, list):
        stores = []

    wants_parking = bool(lookup.get("wants_parking")) or "停车" in content
    wants_route = bool(lookup.get("wants_route")) or any(
        term in content for term in ["地址", "导航", "哪里", "怎么过去", "位置", "发给我", "发我", "发一个"]
    )
    wants_status = bool(lookup.get("wants_status")) or any(
        term in content for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业时间", "几点开", "几点关"]
    )
    recommended_store = lookup.get("recommended_store")
    if not isinstance(recommended_store, dict):
        recommended_store = {}
    recommendation_reason = str(lookup.get("recommendation_reason") or "").strip()
    location_preference = str(lookup.get("location_preference") or "").strip()

    facts: list[str] = []
    reply_points: list[str] = []
    missing_slots: list[str] = []
    city = str(lookup.get("city") or callbacks.extract_city(content) or "").strip()

    if stores:
        if recommended_store:
            recommendation = f"推荐门店：{recommended_store.get('name')}，{recommended_store.get('address')}"
            if location_preference:
                recommendation += f"；位置偏好：{location_preference}"
            if recommendation_reason:
                recommendation += f"；推荐原因：{recommendation_reason}"
            driving = _store_driving_text(recommended_store)
            if driving:
                recommendation += f"；车程参考：{driving}"
            facts.append(recommendation)

        for store in stores[:3]:
            if not isinstance(store, dict):
                continue
            parts = [f"{store.get('name')}：{store.get('address')}"]
            if wants_status and store.get("business_hours"):
                parts.append(f"营业时间{store.get('business_hours')}")
            if wants_status and store.get("status_summary"):
                parts.append(str(store.get("status_summary")))
            if wants_route and store.get("map_url"):
                parts.append(f"导航链接{store.get('map_url')}")
            driving = _store_driving_text(store)
            if driving:
                parts.append(f"车程参考{driving}")
            if wants_parking:
                parking = callbacks.parking_text(store)
                if parking:
                    parts.append(parking)
            facts.append("；".join(part for part in parts if part))

        if len(stores) == 1 and isinstance(stores[0], dict):
            store = stores[0]
            if wants_status and store.get("status_summary"):
                reply_points.append("本轮询问门店营业状态，直接引用门店状态和营业时间事实，不要猜测未确认的停业/闭店。")
            elif wants_route and store.get("map_url"):
                reply_points.append("本轮询问地址或导航，直接给门店名称、地址和导航链接。")
            elif wants_parking and callbacks.parking_text(store):
                reply_points.append("本轮询问停车，直接给门店名称和停车事实。")
            else:
                reply_points.append("本轮询问单家门店，直接给门店名称和地址事实。")
        elif recommended_store:
            reply_points.append(
                f"已匹配到{len(stores)}家门店；优先推荐{recommended_store.get('name')}；"
                "最终回复应说明推荐原因，并简要列出另外几家备选。"
            )
            if wants_route or wants_parking:
                reply_points.append("客户已经在要地址、导航或停车时，不要再反问方便哪家，直接发推荐门店的完整资料。")
            else:
                reply_points.append("客户只要附近门店时，默认先推荐最方便的一家，不要先反问客户选哪家。")
        else:
            reply_points.append(f"已匹配到{len(stores)}家门店；最终回复应按客户本轮问题列出门店信息。")
    else:
        if city:
            reply_points.append(f"{city}未匹配到可用门店事实；不能拿其他城市门店代替回复。")
        else:
            missing_slots.append("城市或门店")
            reply_points.append("缺少城市或门店事实；最终回复只问一个必要位置问题。")

    return {
        "skill": "store",
        "intent": "store_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": [],
        "suggested_next_step": "发送客户本轮明确询问的门店信息" if stores else "确认城市或门店",
    }


def _store_driving_text(store: dict[str, Any]) -> str:
    driving = store.get("driving_time") if isinstance(store, dict) else None
    if not isinstance(driving, dict):
        return ""
    summary = str(driving.get("summary") or "").strip()
    if summary:
        return summary
    output = driving.get("raw_output")
    if isinstance(output, dict):
        for key in ["duration", "driving_time", "time", "text", "output"]:
            value = output.get(key)
            if value:
                return str(value).strip()
    return ""
