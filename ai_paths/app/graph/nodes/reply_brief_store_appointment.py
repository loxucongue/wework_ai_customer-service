from __future__ import annotations

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
                }
                brief["available_facts"]["recommended_store"] = recommendation_fact
                brief["known_facts"].append(
                    "门店推荐：" + "，".join(
                        part
                        for part in [
                            str(recommendation_fact.get("name") or ""),
                            str(recommendation_fact.get("address") or ""),
                            str(recommendation_fact.get("reason") or ""),
                        ]
                        if part
                    )
                )
            brief["must_answer"].append("本轮是门店问题，直接回答匹配到的门店；如果客户指定城市，不能回复其他城市门店。")
            if len(stores) > 1:
                if recommended_store:
                    brief["must_answer"].append("客户有位置偏好时，不要只列门店清单；先说明优先推荐哪家和原因，再简短列出其他备选门店。")
                    brief["follow_up"] = "如果客户接着说“这家/推荐那家/把这家发我”，默认指推荐门店。"
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


def apply_appointment_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    if intent_set & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        brief["must_answer"].append("本轮是预约相关问题，围绕已有预约、可约时间、改约或取消诉求回答；不要主动制造预约。")
        active_task = state.get("active_task") or {}
        if isinstance(active_task, dict) and active_task.get("type") == "appointment_visit":
            brief["available_facts"]["active_task"] = active_task
            slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
            if slots:
                slot_text = "、".join(f"{key}={value}" for key, value in slots.items())
                brief["known_facts"].append(f"当前未完成预约任务已知信息：{slot_text}")
            missing = active_task.get("missing_slots") if isinstance(active_task.get("missing_slots"), list) else []
            if missing:
                brief["known_facts"].append("当前预约只缺：" + "、".join(str(item) for item in missing[:3]))
                if "日期" in missing:
                    brief["must_answer"].append("当前预约还缺日期；只能请客户确认哪一天，不能说某个时间目前可约或有空档。")
                    brief["do_not_say"].extend(["目前可约", "是可约的", "有可约时间", "有空档", "可以预约"])
            brief["must_answer"].append("如果客户本轮只是确认日期、时间或问“约好吗”，必须承接这个预约任务；不要再问项目需求、毛孔/肤色等问题。")
            brief["do_not_say"].extend(["你最想先改善哪方面", "先改善哪方面", "毛孔还是肤色", "有什么皮肤问题"])
        available = tool_results.get("available_time") or {}
        if isinstance(available, dict):
            slots_value = available.get("slots") or {}
            slot_list = callbacks.available_slot_list(slots_value)
            preferred_time = ""
            if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict):
                preferred_time = str(active_task["known_slots"].get("visit_time") or "")
            if slot_list:
                brief["available_facts"]["available_time_slots"] = slot_list[:12]
                brief["known_facts"].append("当前接口返回可约时间：" + "、".join(slot_list[:12]))
                if preferred_time:
                    brief["available_facts"]["customer_preferred_time"] = preferred_time
                    brief["available_facts"]["preferred_time_available"] = preferred_time in slot_list
                    if preferred_time in slot_list:
                        brief["must_answer"].append(f"客户偏好的{preferred_time}在可约时间内，要直接说可以按这个时间继续确认。")
                    else:
                        brief["must_answer"].append(f"客户偏好的{preferred_time}不在当前可约时间内，必须说明这一点，并给出接口返回的可选时间。")
                        brief["answer_first"].append(
                            f"{preferred_time}这边暂时没看到可约；当前接口返回可选时间是{ '、'.join(slot_list[:8]) }。"
                        )
                        for variant in callbacks.time_text_variants(preferred_time):
                            brief["do_not_say"].extend([
                                f"{variant}还有空位",
                                f"{variant}是可以的",
                                f"{variant}可以预约",
                                f"{variant}可约",
                            ])
                        brief["do_not_say"].extend([
                            f"{preferred_time}还有空位",
                            "5点还有空位",
                            "5点是可以的",
                            "已经确认预约",
                            "预约成功",
                            "帮你预留",
                        ])
            elif available.get("missing"):
                brief["known_facts"].append("预约查询还缺：" + "、".join(str(item) for item in available.get("missing", [])[:3]))
            elif available.get("error"):
                brief["known_facts"].append("可约时间接口异常，不能承诺已约好或可直接到店。")
        appointment_context = callbacks.appointment_context_sentence(state) if callbacks.should_show_appointment_context(state) else ""
        if appointment_context:
            brief["known_facts"].append(appointment_context)
    else:
        brief["do_not_say"].extend(["你已有预约", "已有预约记录", "查可约时间", "约时间"])
