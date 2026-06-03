from __future__ import annotations

import re
from typing import Any

from app.graph import planner_helpers, reply_filters, task_state
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.reply_quality_appointment import claims_unavailable_preferred_time_available
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


def check_forbidden_and_context(
    state: AgentState,
    text: str,
    intents: set[str],
    content: str,
    project: str,
    image_info: dict[str, Any],
    known_visible: list[Any],
    message_count: int,
    callbacks: ReplyQualityCallbacks,
) -> bool | None:
        if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
            if any(term in text for term in ["点状斑", "肤色改善", "淡斑", "修护", "术后修护", "斑点深浅"]):
                return True
        if "after_sales" in intents and any(term in content for term in ["三天", "3天", "第三天"]) and any(term in content for term in ["泛红", "有点红", "发红", "红"]):
            if not any(term in text for term in ["三天", "保湿", "防晒", "流脓", "发烧"]):
                return True
        if intents & {"price_inquiry", "campaign_inquiry"} and callbacks.is_broad_price_category(callbacks.contextual_price_project(state)) and any(term in text for term in ["胶原类项目", "PDRN", "喷雾", "洁面", "精华液", "晶钻霜"]):
            return True
        if callbacks.has_confirmed_spot_goal(state) and any(term in text for term in ["你最想先改善哪一点", "最想先改善哪一点", "主要想改善哪一点"]):
            return True
        if callbacks.has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            if any(term in text for term in ["果酸", "焕肤"]) and not callbacks.tool_results_contain(state, "果酸"):
                return True
        hard_forbidden = [
            "预留",
            "留名额",
            "留个名额",
            "留一个名额",
            "锁位",
            "锁定这个时段",
            "先留着",
            "留着这个时段",
            "把这个时段留着",
            "为你锁定",
            "帮你锁位",
            "帮您锁位",
            "马上帮你锁位",
            "帮你留一下",
            "帮您留一下",
            "给你留一下",
            "给您留一下",
            "电话跟您确认",
            "电话联系",
            "电话确认",
            "安排合适的医生",
            "安排医生",
            "安排首次护理",
            "同步安排首次护理",
            "持证医生",
            "持证皮肤科医生",
            "持证医疗机构",
            "专业医生",
            "官方认证",
            "药监局认证",
            "国家药监局认证",
            "所有门店都持有",
            "所有门店都是正规注册",
            "所有门店都是持证合规",
            "所有项目用的都是",
            "所有医美项目",
            "持证合规经营",
            "正规注册的医美机构",
            "卫生许可证",
            "持证皮肤治疗师",
            "持证皮肤管理师",
            "专业治疗师",
            "李技师",
            "8年激光",
            "专业仪器",
            "卫健委核发",
            "卫健委备案",
            "国家药监局备案",
            "药监局备案",
            "医生资质",
            "所有操作",
            "专业顾问跟进",
            "安排专业的顾问",
            "到店后直接找我",
            "肯定有效",
            "包效果",
            "一定有效",
            "正规进口设备",
            "认证耗材",
            "产品授权书",
            "器械备案信息",
            "资质材料发你",
            "发你核对",
            "让店长直接",
            "多年皮秒实操经验",
            "我把营业执照发你",
            "把营业执照发你",
            "发送营业执照",
            "营业执照发你",
            "直接发执照",
            "医生",
            "医生会先看",
            "医生面诊",
            "专业医生",
            "可以放心",
            "您放心",
            "你放心",
            "放心哦",
            "不用担心",
            "别担心",
            "真人客服",
            "我是人工",
            "不是AI",
            "不是机器人",
        ]
        if any(term in text for term in hard_forbidden):
            return True
        soft_promise_terms = [
            "做完就能看到明显变化",
            "做完后会有明显变化",
            "做完后能看到明显变化",
            "会有明显变化",
            "效果非常好",
            "大部分顾客一次效果就很理想",
            "一次效果就很理想",
        ]
        if any(term in text for term in soft_promise_terms):
            return True
        if not callbacks.has_actual_image_context(state) and any(term in text for term in ["你发的图片", "您发的图片", "从你发的图片", "从您发的图片", "结合照片", "前面照片", "照片里", "照片/描述", "发的照片"]):
            return True
        diagnosis_terms = ["雀斑", "晒斑", "黄褐斑", "皮炎", "感染", "玫瑰痤疮", "毛囊炎"]
        if any(term in text for term in diagnosis_terms):
            return True
        if "trust_issue" not in intents and any(term in text for term in ["医疗机构执业许可证", "执业许可证", "资质图片", "正规资质"]):
            return True
        if "store_inquiry" not in intents and not callbacks.is_strong_multi_recap_request(content) and any(term in text for term in ["地址是：", "停车场", "直接导航到"]):
            return True
        if callbacks.is_strong_multi_recap_request(content) and any(
            term in text
            for term in [
                "可约时段",
                "可预约时间",
                "帮你确认时间",
                "你看哪个时间更方便",
                "哪个时间更方便",
                "帮你查时间",
                "继续确认接待",
            ]
        ):
            return True
        if callbacks.is_strong_multi_recap_request(content) and not callbacks.asks_other_store_options(content):
            unrelated_store_terms = [
                "其他门店",
                "其它门店",
                "其他店",
                "其它店",
                "一并发你",
                "也可以发你",
                "再给你列",
                "更多门店",
                "浦东二店",
                "虹口店",
                "嘉定店",
                "你看哪家更方便",
                "哪家更方便",
            ]
            if any(term in text for term in unrelated_store_terms):
                return True
        if "化妆" in content:
            if "空腹" in text:
                return True
            if any(term in text for term in ["不建议化妆", "建议不化妆", "不要化妆", "不能化妆"]) and "淡妆" not in text and "素颜" not in text:
                return True
        if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
            if any(term in text for term in ["面诊名额", "帮您约", "帮你约", "确认具体时间", "什么时候方便呢", "近期可约时段", "可约时段", "可预约时间", "哪个时间更方便", "帮你看看近期", "帮您看看近期", "安排到店咨询", "到店咨询", "到店进一步确认", "到店后确认", "到店再确认", "面诊确认", "面诊后确认"]):
                return True
        if not callbacks.should_show_appointment_context(state):
            if any(term in text for term in ["已有预约", "已有预约记录", "预约记录：", "你这边已有预约"]):
                return True
        return None

def check_store_appointment_price(state: AgentState, text: str, intents: set[str], content: str, project: str, image_info: dict[str, Any], known_visible: list[Any], callbacks: ReplyQualityCallbacks) -> bool | None:
        if "store_inquiry" in intents:
            city = callbacks.extract_city(content)
            lookup = state.get("tool_results", {}).get("store_lookup") or {}
            stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
            if city and city not in text:
                return True
            if city and not stores and any(other_city in text for other_city in CITY_NAMES if other_city != city):
                return True
            if "停车" in content and "停车场" not in text:
                return True
            if "地址" in content and "地址" not in text and "厦门市" not in text:
                return True
        if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            active_task = state.get("active_task") or {}
            slots = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
            missing = active_task.get("missing_slots") if isinstance(active_task, dict) and isinstance(active_task.get("missing_slots"), list) else []
            if "日期" in missing and any(term in text for term in ["目前可约", "是可约的", "有可约时间", "有空档", "可以预约"]):
                return True
            if not slots.get("visit_date_value") and "今天" not in content and "今天" in text:
                return True
            available = state.get("tool_results", {}).get("available_time") or {}
            if isinstance(available, dict) and available.get("slots") and not re.search(r"\d{1,2}:\d{2}", text):
                return True
            if isinstance(available, dict) and available.get("error") and any(term in text for term in ["有空档", "可以安排", "可以预约"]):
                return True
            if callbacks.is_direct_arrival_question(content) and not any(
                term in text for term in ["不建议直接", "先别直接", "直接过去可能不太方便", "不太方便", "不要直接"]
            ):
                return True
        if "after_sales" in intents and any(term in text for term in ["是正常的", "属于正常", "不用太担心", "不用担心"]):
            return True
        if "price_inquiry" in intents:
            if any(term in text for term in ["稍后同步给你", "稍后发你", "回头给你", "我去问下再回复你"]):
                return True
            if any(term in text for term in ["稍后同步", "稍后给你", "稍后再给", "同步参考信息", "同步给你"]):
                return True
            need_text = " ".join([content, *map(str, known_visible), str(image_info.get("image_desc") or "")])
            if (
                any(term in need_text for term in ["斑", "点状", "色沉", "肤色不均", "暗沉"])
                and not any(term in need_text for term in ["敏感", "泛红", "屏障", "刺痛", "干痒", "红血丝"])
                and any(term in text for term in ["舒缓修护", "屏障重建", "敏感修护", "修护方向", "修护", "泛红"])
            ):
                return True
            if reply_filters.has_unsupported_no_price_commitment(text):
                return True
            if callbacks.lacks_price_answer_for_price_question(state, text):
                return True
            if project and f"没有{project}项目" in text:
                return True
            if project == "皮秒" and ("光子嫩肤或者水光" in text or "光子嫩肤或水光" in text):
                return True
            if project and project not in text and not re.search(r"\d+\s*元?", text):
                direction_names = callbacks.project_direction_names_from_state(state)
                has_replacement_direction = any(name and name in text for name in direction_names)
                has_broad_spot_direction = project in {"淡斑", "祛斑"} and any(
                    term in text for term in ["色素淡化", "肤色改善", "斑点", "色沉"]
                )
                if not (has_replacement_direction or has_broad_spot_direction):
                    return True
            if not re.search(r"\d+\s*元?", text) and any(term in text for term in ["具体价格要看", "价格要看", "准确价格", "配置"]) and not callbacks.has_no_price_fact_phrase(text):
                return True
            if reply_filters.asks_daily_single_price(content) and "日常单次" not in text and "日常价" not in text:
                return True
            if callbacks.has_price_objection(content):
                if not reply_filters.has_budget_or_price_answer(text):
                    return True
                if reply_filters.is_project_only_after_price_objection(text):
                    return True
            if (
                not callbacks.is_strong_multi_recap_request(content)
                and not intents & {"store_inquiry", "appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
                and not task_state.is_active_appointment_task(state)
            ):
                if any(term in text for term in ["所在城市", "附近门店", "门店优惠", "更具体的优惠", "优惠信息", "到店时间", "徐汇", "静安", "浦东", "哪家更方便", "对应门店", "门店的老师", "常去哪个区"]):
                    return True
        return None

def check_final_intent_rules(state: AgentState, text: str, intents: set[str], content: str, project: str, image_info: dict[str, Any], known_visible: list[Any], callbacks: ReplyQualityCallbacks) -> bool | None:
        if "trust_issue" in intents and "store_inquiry" not in intents:
            if any(term in text for term in ["徐汇", "静安", "浦东", "哪家更方便", "对接对应门店", "常去哪个区", "最近可约时段", "顺路帮你对接", "想约哪一天", "可预约的时间段", "帮你查当天", "先帮你看这家门店", "最近有空档", "哪家门店最近有空档", "看看哪家上海门店最近有空档"]):
                return True
        if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
            if any(term in text for term in ["哪天方便到店", "方便到店", "到店面诊", "约个面诊", "约个时间到店", "面诊下皮肤状态"]):
                return True
            if any(term in text for term in ["想约哪一天", "查当天可预约", "可预约的时间段", "近期可预约", "最近可约", "你约的是", "已约的是", "确认这个时段", "是否还空着", "换其他时间"]):
                return True
        if "trust_issue" in intents and not intents & {"appointment_intent", "appointment_confirm"}:
            if any(term in text for term in ["这家门店", "想约哪一天", "查当天可预约", "可预约的时间段"]):
                return True
        if not intents & {"price_inquiry", "campaign_inquiry", "competitor_compare"}:
            if any(term in text for term in ["新客体验价", "活动价", "日常单次"]):
                return True
        if "case_request" in intents:
            if not any(term in text for term in ["案例", "前后", "对比", "改善参考", "同类"]):
                return True
            if any(term in text for term in ["案例价格", "哪家门店可以看案例"]) and not any(term in text for term in ["同类", "祛斑", "淡斑", "改善"]):
                return True
            if any(term in text for term in ["第1次", "第一次", "第3次", "第三次", "几天后", "几周后", "顾客反馈"]) and not callbacks.tool_results_contain(state, ["真实案例", "案例图", "前后对比"]):
                return True
        if "project_process" in intents:
            if not any(term in text for term in ["流程", "步骤", "操作", "清洁", "检测", "评估", "分钟", "时长", "多久"]):
                return True
        if "ad_price_check" in intents:
            digits = callbacks.extract_price_digits(content)
            if digits and not any(digit in text for digit in digits[:2]):
                return True
            if not any(term in text for term in ["广告", "活动", "预约金", "尾款", "包含", "另收费", "隐形"]):
                return True
            if callbacks.ad_price_without_explicit_project(state, callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))):
                if any(term in text for term in ["确实有", "真实有", "目前有这个活动", "可以按这个价格", "能按这个价格", "就是这个活动"]):
                    return True
        if claims_unavailable_preferred_time_available(state, text, callbacks):
            return True
        if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            available = state.get("tool_results", {}).get("available_time") or {}
            if isinstance(available, dict) and callbacks.available_slot_list(available.get("slots") or {}) and re.search(r"\d{1,2}:\d{2}", text):
                return False
        if callbacks.too_similar_to_recent_assistant_reply(state, text):
            return True
        return False
        return None
