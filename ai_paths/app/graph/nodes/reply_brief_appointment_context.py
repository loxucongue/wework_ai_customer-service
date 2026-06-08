from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def apply_appointment_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    appointment_context = callbacks.appointment_context_sentence(state) if callbacks.should_show_appointment_context(state) else ""
    if appointment_context:
        brief["known_facts"].append(appointment_context)
        brief["available_facts"]["active_order_context"] = appointment_context
        if "store_inquiry" in intent_set:
            brief["must_answer"].append("如果客户当前已有进行中的预约/订单，且本轮是在问地址、门店、路线或停车，只轻提醒一次，再直接回答当前门店问题。")
            brief["must_answer"].append("提醒已有记录时，不要打断客户，也不要改成重新预约、重新收集项目或继续推预约。")
            brief["follow_up"] = "先解决当前门店问题；只有客户继续追问时，再顺带承接原记录。"
        elif not (intent_set & {"appointment_confirm", "appointment_change", "appointment_cancel", "appointment_intent"}):
            brief["must_answer"].append("只有当前活动记录和这轮问题直接相关时，才轻带一句已有记录；不要反复提醒。")
    if intent_set & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        brief["must_answer"].append("本轮是预约相关问题，围绕已有预约、可约时间、改约或取消诉求回答；不要主动制造预约。")
        if any(term in content for term in ["报名", "先报名", "帮我登记", "登记一下", "留个名额", "留名额", "保留优惠", "先保留"]):
            brief["answer_first"].append("可以的，我先帮你把这个活动名额登记上。")
            brief["must_answer"].append("客户说报名、登记或保留优惠时，不要回普通问候，也不要只说想预约；要直接进入登记节奏。")
            brief["follow_up"] = "优先收一个最关键字段：已知城市/门店就问时间，未知城市/门店就问城市或方便过去的区域。"
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
        opening = tool_results.get("appointment_opening") or {}
        action = tool_results.get("appointment_action") or {}
        if isinstance(opening, dict) and opening:
            facts = opening.get("facts") if isinstance(opening.get("facts"), dict) else {}
            status = str(opening.get("status") or "")
            opening_fact = {
                "status": status,
                "order_id": opening.get("order_id") or "",
                "store_id": facts.get("store_id") or "",
                "store_name": facts.get("store_name") or "",
                "appointment_date": facts.get("date") or "",
                "appointment_time": facts.get("time") or "",
                "prepay": facts.get("prepay") or "",
                "missing": opening.get("missing") or [],
                "error": opening.get("error") or "",
            }
            brief["available_facts"]["appointment_opening"] = opening_fact
            brief["do_not_say"].extend(["已预约成功", "预约成功", "锁位", "锁定名额", "已预留", "留好名额"])
            if status in {"created", "dry_run_created"}:
                brief["known_facts"].append(
                    "预约金订单/预约入口信息已创建："
                    + "、".join(
                        str(item)
                        for item in [
                            opening_fact["store_name"] or opening_fact["store_id"],
                            opening_fact["appointment_date"],
                            opening_fact["appointment_time"],
                            f"预约金{opening_fact['prepay']}" if opening_fact["prepay"] else "",
                        ]
                        if item
                    )
                )
                brief["must_answer"].append(
                    "已创建的是预约金订单或预约入口信息；客户可见回复只能说接下来会发预约入口/小程序，客户按页面确认，不要说已预约成功或已锁位。"
                )
            elif status == "needs_customer_confirmation":
                brief["known_facts"].append("预约开单信息已基本齐全，但客户还没有明确确认开单。")
                brief["must_answer"].append("必须复述门店、日期、时间和预约金，请客户确认是否按这些信息开预约入口；不能直接说已创建。")
            elif status == "preferred_time_unavailable":
                brief["known_facts"].append("客户确认的时间不在可约时间内，不能创建预约金订单。")
            elif status in {"cannot_create", "platform_unavailable", "create_failed", "error"}:
                brief["known_facts"].append("预约金订单创建未成功，需要门店或专业同事核对后继续处理。")
                brief["must_answer"].append("不要承诺已开单或已约好；应说明我帮客户同步给门店同事核对。")
        if isinstance(action, dict) and action:
            action_facts = action.get("facts") if isinstance(action.get("facts"), dict) else {}
            action_status = str(action.get("status") or "")
            action_operation = str(action.get("operation") or "")
            brief["available_facts"]["appointment_action"] = {
                "operation": action_operation,
                "status": action_status,
                "order_id": action_facts.get("order_id") or "",
                "store_id": action_facts.get("store_id") or "",
                "store_name": action_facts.get("store_name") or "",
                "appointment_date": action_facts.get("date") or "",
                "appointment_time": action_facts.get("time") or "",
                "available_time_slots": action_facts.get("available_time_slots") or [],
                "preferred_time_available": action_facts.get("preferred_time_available"),
                "missing": action.get("missing") or [],
                "error": action.get("error") or "",
            }
            if action_status in {"scheduled", "dry_run_scheduled"}:
                brief["known_facts"].append("当前订单已继续走排客处理，客户可按后续门店确认继续承接。")
                brief["must_answer"].append("不要说床位已经绝对锁定或最终预约成功，只能说这边已经继续帮客户按这个时间往下安排。")
            elif action_status in {"changed", "dry_run_changed"}:
                brief["known_facts"].append("当前订单已按新的日期继续做改约处理。")
                brief["must_answer"].append("不要说改约绝对成功，只能说这边已按新的时间继续帮客户调整。")
            elif action_status in {"cancelled", "dry_run_cancelled"}:
                brief["known_facts"].append("当前订单的排客安排已取消。")
                brief["must_answer"].append("取消后只说明当前安排已撤下，如客户还想来可以重新确认时间。")
            elif action_status == "preferred_time_unavailable":
                brief["known_facts"].append("客户偏好的时间当前没有落在可约时段里。")
                brief["must_answer"].append("先解释这个时间暂时没看到，再给当前可选时间；不要继续说这个时间可以。")
            elif action_status == "platform_contract_error":
                brief["known_facts"].append("改约接口的真实参数还需要平台侧确认，当前不能直接承诺已经改约成功。")
                brief["must_answer"].append("应说明这边先帮客户把新时间同步给门店或专业同事核对，确认后继续安排。")
            elif action_status in {"error", "platform_unavailable"}:
                brief["known_facts"].append("预约动作没有完成，需要门店或专业同事继续核对。")
                brief["must_answer"].append("不要承诺排客、改约或取消已成功。")
    else:
        if not appointment_context:
            brief["do_not_say"].extend(["你已有预约", "已有预约记录", "查可约时间", "约时间"])
