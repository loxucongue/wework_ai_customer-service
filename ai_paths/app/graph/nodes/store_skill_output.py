from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.sales_talk_kb_parsing import first_sales_talk_slice


@dataclass(frozen=True)
class StoreSkillCallbacks:
    extract_city: Callable[[str], str]
    parking_text: Callable[[dict[str, Any]], str]


def store_skill_output(content: str, tool_results: dict[str, Any], callbacks: StoreSkillCallbacks) -> dict[str, Any]:
    """Build factual store-skill output for the final reply model."""

    sales_talk = first_sales_talk_slice(tool_results)
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
    city_only_refinement = _should_refine_city_only_location(
        content=content,
        city=city,
        stores=stores,
        recommended_store=recommended_store,
        location_preference=location_preference,
        wants_route=wants_route,
        wants_parking=wants_parking,
        wants_status=wants_status,
    )

    if stores:
        if city_only_refinement:
            facts.append(f"当前已知客户所在城市：{city}；该城市当前可接待门店共{len(stores)}家。")
            reply_points.append("客户目前只给了城市，还没有更细位置；不要先把全部门店清单丢给客户。")
            reply_points.append("先用一句很短的话确认位置偏好，例如思明、湖里、机场附近哪一片，再帮客户直接缩到最近或更方便的一家。")
            _inject_sales_talk_guidance(facts, reply_points, sales_talk)
            return {
                "skill": "store",
                "intent": "store_inquiry",
                "facts": facts,
                "reply_points": reply_points,
                "missing_slots": ["区域或位置偏好"],
                "risk_flags": [],
                "suggested_next_step": "确认更细位置后直接推荐最近门店",
            }
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

        for store in stores:
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
        if city and not location_preference and not wants_route and not wants_parking and not wants_status:
            reply_points.append("客户当前只是按城市泛问门店时，首次回复要列出该城市全部已匹配门店，不要只展示前三家。")
    else:
        if city:
            reply_points.append(f"{city}当前没有拿到可直接引用的实时门店结果；不能拿旧记录、本地目录或其他城市门店代替回复。")
            reply_points.append("最终回复只能如实说明暂时没拉到实时门店信息，必要时再按城市、区域或地标刷新一次。")
        else:
            missing_slots.append("城市或门店")
            reply_points.append("缺少城市或门店事实；最终回复只问一个必要位置问题。")
    _inject_sales_talk_guidance(facts, reply_points, sales_talk)

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
    return str(driving.get("summary") or "").strip()


def _should_refine_city_only_location(
    *,
    content: str,
    city: str,
    stores: list[dict[str, Any]],
    recommended_store: dict[str, Any],
    location_preference: str,
    wants_route: bool,
    wants_parking: bool,
    wants_status: bool,
) -> bool:
    if not city or len(stores) <= 1 or recommended_store or location_preference:
        return False
    if wants_route or wants_parking or wants_status:
        return False
    text = str(content or "").strip()
    if not text:
        return False
    generic_terms = [
        city,
        "我在",
        "在",
        "这边",
        "附近",
    ]
    simplified = text
    for term in generic_terms:
        simplified = simplified.replace(term, "")
    simplified = simplified.strip(" ，。！？?~～")
    return not simplified


def _inject_sales_talk_guidance(
    facts: list[str],
    reply_points: list[str],
    sales_talk: dict[str, str],
) -> None:
    if not sales_talk:
        return
    if sales_talk.get("scene_type"):
        facts.append(f"销售话术场景：{sales_talk['scene_type']}")
    if sales_talk.get("target"):
        facts.append(f"承接目标：{sales_talk['target']}")
    if sales_talk.get("sample_reply"):
        reply_points.insert(0, f"优先参考这种门店承接节奏：{sales_talk['sample_reply']}")
    if sales_talk.get("next_step"):
        reply_points.append(f"下一步建议：{sales_talk['next_step']}")
    if sales_talk.get("forbidden"):
        facts.append(f"禁用表达：{sales_talk['forbidden']}")
