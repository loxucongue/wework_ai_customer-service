from __future__ import annotations

from typing import Any


def with_location_recommendation(result: dict[str, Any], location_preference: str) -> dict[str, Any]:
    if not location_preference:
        return result
    stores = result.get("stores") if isinstance(result, dict) else []
    if not isinstance(stores, list) or not stores:
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
    if location_preference in {"厦门高崎机场", "机场附近"}:
        if any(term in text for term in ["高崎", "机场"]):
            return 0
        if any(term in text for term in ["湖里", "嘉园", "安岭", "钟宅", "蔡塘"]):
            return 2
        if "集美" in text:
            return 4
        if any(term in text for term in ["思明", "厦禾", "国骏"]):
            return 8
    if location_preference == "蔡塘地铁站":
        if "蔡塘" in text:
            return 0
        if any(term in text for term in ["湖里", "嘉园", "创新科技园"]):
            return 1
        if "集美" in text:
            return 5
    if location_preference == "南山科技园":
        if any(term in text for term in ["南山", "科技园"]):
            return 0
        if "福田" in text:
            return 3
        if "宝安" in text:
            return 4
        if "罗湖" in text:
            return 6
    if location_preference == "火车站附近":
        if any(term in text for term in ["火车站", "高铁", "动车"]):
            return 0
        if any(term in text for term in ["中心", "商圈", "地铁"]):
            return 4
    return 99


def recommendation_reason(store: dict[str, Any], location_preference: str) -> str:
    name = str(store.get("name") or "这家门店").strip()
    address = str(store.get("address") or "").strip()
    if location_preference in {"厦门高崎机场", "机场附近"}:
        if any(term in f"{name} {address}" for term in ["湖里", "嘉园", "高崎", "机场", "安岭", "钟宅"]):
            return f"客户偏好机场附近，按当前门店地址看，{name}在湖里方向，建议优先对比；具体距离以导航为准。"
        return f"客户偏好机场附近，可优先对比{name}；具体距离以导航为准。"
    if location_preference == "蔡塘地铁站":
        if any(term in f"{name} {address}" for term in ["湖里", "嘉园", "蔡塘", "创新科技园"]):
            return f"客户提到蔡塘地铁站，按当前门店地址看，可优先对比{name}；具体距离以导航为准。"
        return f"客户提到蔡塘地铁站，可优先对比{name}；具体距离以导航为准。"
    if location_preference == "南山科技园":
        return f"客户在南山科技园附近，优先对比{name}；具体距离以导航为准。"
    if location_preference == "火车站附近":
        return f"客户偏好火车站附近，优先对比{name}；具体距离以导航为准。"
    return f"按客户位置偏好，可优先对比{name}；具体距离以导航为准。"
