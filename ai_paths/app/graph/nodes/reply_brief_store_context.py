from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def apply_store_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    if "store_inquiry" in intent_set:
        lookup = tool_results.get("store_lookup") or {}
        stores = lookup.get("stores") if isinstance(lookup, dict) else []
        wants_parking = bool(lookup.get("wants_parking")) or "停车" in content
        wants_route = bool(lookup.get("wants_route")) or any(term in content for term in ["地址", "导航", "哪里", "怎么过去", "位置", "发给我", "发我", "发一下"])
        wants_status = bool(lookup.get("wants_status")) or any(term in content for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业时间", "几点开", "几点关"])
        recommended_store = lookup.get("recommended_store") if isinstance(lookup, dict) and isinstance(lookup.get("recommended_store"), dict) else {}
        recommendation_reason = str(lookup.get("recommendation_reason") or "").strip() if isinstance(lookup, dict) else ""
        location_preference = str(lookup.get("location_preference") or "").strip() if isinstance(lookup, dict) else ""
        if isinstance(stores, list) and stores:
            recommendation_followup = _is_store_recommendation_followup(content)
            single_store_fact_query = len(stores) == 1 and (wants_parking or wants_route or wants_status)
            store_facts = []
            for store in stores[:3]:
                if not isinstance(store, dict):
                    continue
                bits = [str(store.get("name") or "").strip(), str(store.get("address") or "").strip()]
                if wants_status and store.get("business_hours"):
                    bits.append(f"营业时间{store.get('business_hours')}")
                if wants_status and store.get("status_summary"):
                    bits.append(str(store.get("status_summary")))
                driving = _store_driving_text(store)
                if driving:
                    bits.append(f"车程参考{driving}")
                parking = callbacks.parking_text(store) if wants_parking else ""
                if parking:
                    bits.append(parking)
                brief["known_facts"].append("门店：" + "，".join(bit for bit in bits if bit))
                item = {
                    "name": store.get("name"),
                    "address": store.get("address"),
                    "map_url": store.get("map_url") if wants_route else "",
                    "parking": parking,
                    "business_hours": store.get("business_hours") if wants_status else "",
                    "status_summary": store.get("status_summary") if wants_status else "",
                    "status_code": store.get("status_code"),
                    "shore_show_code": store.get("shore_show_code"),
                    "is_pause": store.get("is_pause"),
                    "driving_time": driving,
                }
                store_facts.append(item)
                if len(stores) == 1:
                    if wants_status:
                        status_values = [
                            str(item.get("name") or ""),
                            str(item.get("status_summary") or ""),
                            f"营业时间{item.get('business_hours')}" if item.get("business_hours") else "",
                        ]
                        if not item.get("status_summary") and any(term in content for term in ["还开", "还营业", "开门", "关门", "闭店", "停业"]):
                            status_values.append("当前资料未显示暂停接待")
                        brief["answer_first"].append("门店营业事实：" + "，".join(value for value in status_values if value))
                    elif wants_route and item.get("map_url"):
                        brief["answer_first"].append(
                            f"门店导航事实：{item.get('name')}地址：{item.get('address')}；导航：{item.get('map_url')}"
                        )
                    elif wants_parking and item.get("parking"):
                        brief["answer_first"].append(
                            f"门店停车事实：{item.get('name')}；{item.get('parking')}"
                        )
            brief["available_facts"]["stores"] = store_facts
            if recommended_store:
                recommendation_fact = {
                    "name": recommended_store.get("name"),
                    "address": recommended_store.get("address"),
                    "location_preference": location_preference,
                    "reason": recommendation_reason,
                    "map_url": recommended_store.get("map_url") if wants_route else "",
                    "parking": callbacks.parking_text(recommended_store) if wants_parking else "",
                    "business_hours": recommended_store.get("business_hours") if wants_status else "",
                    "driving_time": _store_driving_text(recommended_store),
                }
                brief["available_facts"]["recommended_store"] = recommendation_fact
                brief["known_facts"].append(
                    "门店推荐：" + "，".join(
                        part
                        for part in [
                            str(recommendation_fact.get("name") or ""),
                            str(recommendation_fact.get("address") or ""),
                            str(recommendation_fact.get("reason") or ""),
                            f"车程参考{recommendation_fact.get('driving_time')}" if recommendation_fact.get("driving_time") else "",
                        ]
                        if part
                    )
                )
                if recommendation_followup:
                    recommended_name = str(recommendation_fact.get("name") or "").strip()
                    recommended_reason = str(recommendation_fact.get("reason") or "").strip()
                    if recommended_name:
                        answer = f"优先推荐{recommended_name}"
                        if recommended_reason:
                            answer += f"，{recommended_reason}"
                        if recommendation_fact.get("driving_time"):
                            answer += f"，车程参考{recommendation_fact.get('driving_time')}"
                        brief["answer_first"].append(answer + "。")
                    brief["must_answer"].append("客户是在已知多家门店后让你直接推荐一家，回复只保留推荐门店和一句原因，不要重复整段门店列表。")
                    brief["must_answer"].append("推荐门店后，后续客户说“这家/那家/地址发我/停车发我”时，默认就是这家推荐门店，不要切换成别的门店。")
                    brief["do_not_say"].extend(
                        [
                            "另外还有",
                            "其他可选",
                            "总共有",
                            "匹配到3家门店",
                            "你看哪家更方便",
                            "还有厦门二店",
                            "还有厦门思明店",
                        ]
                    )
                    other_store_names = [
                        str(item.get("name") or "").strip()
                        for item in store_facts
                        if isinstance(item, dict) and str(item.get("name") or "").strip() and str(item.get("name") or "").strip() != recommended_name
                    ]
                    brief["do_not_say"].extend(other_store_names)
            brief["must_answer"].append("本轮是门店问题，直接回答匹配到的门店；如果客户指定城市，不能回复其他城市门店。")
            if len(stores) > 1:
                if recommended_store:
                    brief["must_answer"].append("客户有位置偏好时，不要只列门店清单；先说明优先推荐哪家和原因，再简短列出其他备选门店。")
                    brief["follow_up"] = "如果客户接着说“这家/推荐那家/把这家发我”，默认指推荐门店。"
                elif _is_city_only_store_reply(content, lookup, store_facts):
                    brief["must_answer"].append("客户现在只给了城市，且该城市有多家门店；不要先把整份门店清单丢给客户。")
                    brief["must_answer"].append("先用一句很短的话问更细的位置偏好，例如哪个区、机场附近还是哪一片，好直接缩到最近或更方便的一家。")
                    brief["do_not_say"].extend(["目前有3家门店", "分别是", "你看哪家更方便"])
                else:
                    brief["must_answer"].append("客户问城市门店列表时，列出门店名和地址即可；结尾最多问客户哪家更方便，不主动发散到导航或停车。")
                brief["do_not_say"].extend(
                    [
                        "每家都有",
                        "都有专属停车",
                        "都支持地铁",
                        "地铁直达",
                        "专属停车场",
                        "如果需要",
                        "如需",
                        "需要我帮你发导航",
                        "发具体导航",
                        "导航或停车",
                        "导航和停车",
                    ]
                )
                if not wants_parking:
                    brief["do_not_say"].extend(["停车", "停车信息"])
            if wants_route:
                brief["must_answer"].append("客户问地址/导航/怎么过去时，有导航链接就直接给链接，不要只问要不要发导航。")
            if wants_parking:
                brief["must_answer"].append("客户问停车时，有停车事实就直接给停车场或停车地址。")
            if wants_status:
                brief["must_answer"].append("客户问门店是否营业/关门时，优先用门店状态和营业时间回答；如果状态不是正常可展示，不要说没有通知，要说明当前资料状态需要门店确认。")
                brief["do_not_say"].extend(["电话问一下", "打电话", "建议电话"])
            if _is_store_fact_send_request(content):
                brief["must_answer"].append("客户是在要刚刚选中的这家门店资料，直接发这家店的地址/导航/停车等当前请求的事实，不要再追加预约、时间、更多门店或再次确认方便哪家。")
                if recommended_store:
                    recommended_name = str(recommended_store.get("name") or "").strip()
                    if recommended_name:
                        brief["must_answer"].append(f"这轮门店资料默认发{recommended_name}，不要切换成别的门店。")
                        other_store_names = [
                            str(item.get("name") or "").strip()
                            for item in store_facts
                            if isinstance(item, dict) and str(item.get("name") or "").strip() and str(item.get("name") or "").strip() != recommended_name
                        ]
                        brief["do_not_say"].extend(other_store_names)
                brief["do_not_say"].extend(
                    [
                        "你看哪家更方便",
                        "哪天方便",
                        "要不要预约",
                        "要不要查可约",
                        "还可以看看其他门店",
                        "如果需要",
                        "如需",
                        "需要导航",
                        "需要停车",
                        "需要我发",
                        "随时发你",
                    ]
                )
            if single_store_fact_query:
                brief["must_answer"].append("本轮只问单一门店事实，回答该事实后收住，不要追加新的追问或预约推进。")
                brief["do_not_say"].extend(
                    [
                        "需要我帮你发导航",
                        "需要我帮你发停车",
                        "需要我帮你查可约时间",
                        "如果需要",
                        "如需",
                        "要不要",
                        "我可以发",
                        "可以发",
                        "发送到手机",
                        "发到手机",
                        "发送给您手机",
                        "要不要查可约",
                        "要不要预约",
                        "哪天来",
                        "哪天方便",
                        "你看哪家更方便",
                    ]
                )
                if wants_status:
                    brief["do_not_say"].extend(["停车", "导航", "预约", "到店", "可约"])
                elif wants_route and not wants_parking:
                    brief["do_not_say"].extend(["停车", "停车场", "停车信息", "停车地址"])
                elif wants_parking and not wants_route:
                    brief["do_not_say"].extend(["导航链接", "具体路线"])
        else:
            city = lookup.get("city") if isinstance(lookup, dict) else callbacks.extract_city(content)
            if callbacks.store_lookup_missing_city(tool_results):
                brief["known_facts"].append("客户还没有提供城市或具体区域，当前不能匹配具体门店。")
                brief["must_answer"].append("客户泛问门店但缺城市/区域时，只需要请客户补充所在城市或区域；不能说已查到某家门店，也不能说没有查到客户所在区域。")
                brief["do_not_say"].extend(
                    [
                        "目前我们有",
                        "没有查到您所在区域",
                        "暂时没有查到您所在区域",
                        "更倾向的门店类型",
                        "服务方向",
                        "地址、导航和停车信息",
                    ]
                )
            else:
                brief["known_facts"].append(f"按{city or '客户提供的位置'}暂时没有匹配到可直接发送的门店信息。")
            brief["available_facts"]["stores"] = []


def apply_store_recap_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.asks_store_or_address_recap(content):
        brief["must_answer"].append("客户要你把门店/地址再顺一下时，直接把已知门店名和地址带回，不要只回答别的问题。")
        store_summary = callbacks.store_summary_message(state)
        if store_summary:
            brief["answer_first"].append(store_summary)
            if "历史门店地址事实" in store_summary:
                brief["must_answer"].append("历史里已有具体门店地址时，复述地址必须以历史门店为准，不能改用工具新匹配到的其他门店。")
                lookup = state.get("tool_results", {}).get("store_lookup") or {}
                stores = lookup.get("stores") if isinstance(lookup, dict) else []
                if isinstance(stores, list):
                    for store in stores:
                        if not isinstance(store, dict):
                            continue
                        name = str(store.get("name") or "").strip()
                        if name and name not in store_summary:
                            brief["do_not_say"].append(name)
                brief["do_not_say"].extend(["哪家更方便", "更多门店", "一并发你"])


def _is_store_recommendation_followup(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    direct_terms = [
        "直接推荐",
        "推荐一家",
        "帮我选",
        "你选一家",
        "就推荐",
        "推荐一个",
        "方便一点",
        "近一点",
    ]
    return any(term in text for term in direct_terms) or bool(re.search(r"哪家.*方便", text))


def _is_store_fact_send_request(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if "发" not in text:
        return False
    return any(
        term in text
        for term in [
            "发给我",
            "发我",
            "把这家",
            "这家店发",
            "把这家店",
            "把这家发",
            "地址发我",
            "把这家店发给我",
        ]
    )


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


def _is_city_only_store_reply(content: str, lookup: dict[str, Any], store_facts: list[dict[str, Any]]) -> bool:
    city = str(lookup.get("city") or "").strip()
    if not city or len(store_facts) <= 1:
        return False
    if lookup.get("recommended_store") or lookup.get("location_preference"):
        return False
    if bool(lookup.get("wants_route")) or bool(lookup.get("wants_parking")) or bool(lookup.get("wants_status")):
        return False
    text = str(content or "").strip()
    for prefix in ["我在", "人在", "目前在", "现在在", "住在"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    for suffix in ["这边", "这儿", "附近"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = text.strip(" ，。！？?~～")
    return text in {city, f"{city}市"}
