from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


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
