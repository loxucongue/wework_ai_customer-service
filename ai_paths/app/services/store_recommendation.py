from __future__ import annotations

from typing import Any


def with_location_recommendation(result: dict[str, Any], location_preference: str) -> dict[str, Any]:
    if not location_preference:
        return result
    stores = result.get("stores") if isinstance(result, dict) else []
    if not isinstance(stores, list) or not stores:
        return result
    city = str(result.get("city") or "").strip()
    if location_preference == "机场附近" and city and city != "厦门":
        return result
    ranked = sorted(
        [store for store in stores if isinstance(store, dict)],
        key=lambda store: location_preference_rank(store, location_preference),
    )
    if not ranked:
        return result
    best_rank = location_preference_rank(ranked[0], location_preference)
    if best_rank >= 50:
        return result
    recommended = ranked[0]
    output = dict(result)
    output["stores"] = ranked
    output["location_preference"] = location_preference
    output["recommended_store"] = recommended
    output["recommendation_reason"] = recommendation_reason(recommended, location_preference)
    return output


def location_preference_rank(store: dict[str, Any], location_preference: str) -> int:
    text = " ".join(str(store.get(key) or "") for key in ["name", "city", "address", "parking_name", "parking_address"])
    if location_preference == "机场附近":
        if any(term in text for term in ["机场", "高崎"]):
            return 0
        if any(term in text for term in ["百星", "枋湖"]):
            return 1
        if any(term in text for term in ["湖里", "安岭", "钟宅"]):
            return 2
        if any(term in text for term in ["思明", "厦禾", "国骏"]):
            return 8
    return 99


def recommendation_reason(store: dict[str, Any], location_preference: str) -> str:
    name = str(store.get("name") or "这家门店").strip()
    address = str(store.get("address") or "").strip()
    if location_preference == "机场附近":
        if any(term in f"{name} {address}" for term in ["湖里", "枋湖", "百星", "安岭", "钟宅"]):
            return f"客户偏好机场附近，按当前门店地址看，{name}在湖里区方向，比思明区门店更贴近机场区域。"
        return f"客户偏好机场附近，按当前门店地址看，可优先对比{name}。"
    return f"按客户位置偏好，可优先对比{name}。"
