from __future__ import annotations

from typing import Any

from app.graph.planner_intent_meta import extract_city
from app.graph.state import AgentState
from app.services.store_text_constants import AREA_CITY_MAP, CITY_NAMES


SALES_STAGE_LABELS = {
    "opening_intro": "打招呼/介绍",
    "store_paving": "门店/地址铺垫",
    "price_paving": "报价铺垫",
    "quote": "报价",
    "close_order": "逼单/预约引导",
    "collect_info": "预约信息确认",
    "handoff_at_store": "到店转接",
    "service_recovery": "客诉/售后承接",
    "answer_only": "直接解答",
}


def build_sales_strategy(
    state: AgentState,
    intents: list[dict[str, Any]],
    active_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer the current sales stage and reply rhythm from current-turn facts."""
    content = str(state.get("normalized_content") or "").strip()
    intent_set = {str(item.get("intent") or "") for item in intents if isinstance(item, dict)}
    skill_set = {str(item.get("skill") or "") for item in intents if isinstance(item, dict)}
    active = active_task if isinstance(active_task, dict) else state.get("active_task", {})
    known_slots = _known_slots(state, intents, active)
    stage = _sales_stage(content, intent_set, skill_set, active)
    missing_slots = _missing_slots(stage, known_slots)
    ask_policy = _ask_policy(stage, content, known_slots, missing_slots)
    return {
        "sales_stage": stage,
        "stage_label": SALES_STAGE_LABELS.get(stage, stage),
        "reason": _stage_reason(stage, intent_set, known_slots),
        "known_slots": known_slots,
        "missing_slots": missing_slots,
        "ask_policy": ask_policy,
        "next_best_action": _next_best_action(stage, known_slots, missing_slots),
        "push_goal": _push_goal(stage),
        "reply_rhythm": _reply_rhythm(stage, ask_policy),
    }


def _sales_stage(
    content: str,
    intents: set[str],
    skills: set[str],
    active_task: dict[str, Any] | None,
) -> str:
    if intents & {"complaint_refund", "human_request", "after_sales"} or skills & {"handoff", "after_sales"}:
        return "service_recovery"
    if _is_at_store(content):
        return "handoff_at_store"
    if _has_confirmed_booking_signal(content):
        return "collect_info"
    if isinstance(active_task, dict) and active_task.get("type") == "appointment_visit":
        known = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
        if _has_customer_confirmation(content) and _has_opening_ready_slots(known):
            return "collect_info"
        if intents & {"appointment_intent", "appointment_confirm"}:
            return "collect_info"
        return "close_order"
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} or skills & {"appointment"}:
        if _has_arrival_intent(content):
            return "close_order"
        return "collect_info"
    if intents & {"store_inquiry"} or skills & {"store"}:
        return "store_paving"
    if intents & {"price_inquiry", "ad_price_check", "campaign_inquiry", "competitor_compare"} or skills & {"price_consult", "campaign"}:
        if _has_arrival_intent(content):
            return "close_order"
        if any(
            term in content
            for term in ["多少钱", "价格", "费用", "一次", "定金", "预约金", "尾款", "另收费", "加钱", "全款"]
        ):
            return "quote"
        return "price_paving"
    if intents & {"project_inquiry", "image_inquiry", "case_request", "project_process"}:
        return "opening_intro" if _is_early_intro(content) else "price_paving"
    if intents & {"trust_issue"}:
        return "answer_only"
    if intents & {"greeting", "emotion_chat"} or not intents:
        return "opening_intro"
    return "answer_only"


def _known_slots(
    state: AgentState,
    intents: list[dict[str, Any]],
    active_task: dict[str, Any] | None,
) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    active_known = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
    return _compact_slots(
        {
            "city": active_known.get("city")
            or state.get("detected_city")
            or _extract_city_from_content(str(state.get("normalized_content") or ""))
            or basic.get("city")
            or customer_context.get("city")
            or "",
            "store_id": active_known.get("store_id") or state.get("confirmed_store_id") or state.get("store_id") or "",
            "store_name": active_known.get("store_name") or state.get("confirmed_store_name") or state.get("store_name") or "",
            "need": _first_text(profile.get("needs")) or _known_info_contains(intents, ["祛斑", "淡斑", "抗衰", "补水", "毛孔", "暗沉"]),
            "project_direction": _known_info_contains(intents, ["项目", "方向", "淡斑", "色素", "肤色", "补水", "抗衰"]),
            "visit_date": active_known.get("visit_date_label") or active_known.get("visit_date_value") or "",
            "visit_time": active_known.get("visit_time") or "",
            "party_size": active_known.get("party_size") or "",
            "customer_name": active_known.get("customer_name") or customer_context.get("name") or customer_context.get("customer_name") or "",
            "phone": active_known.get("phone") or customer_context.get("phone") or customer_context.get("mobile") or "",
        }
    )


def _missing_slots(stage: str, known_slots: dict[str, Any]) -> list[str]:
    required_by_stage = {
        "store_paving": ["city"],
        "collect_info": ["store_name", "visit_date", "visit_time", "customer_name", "phone"],
        "close_order": ["store_name", "visit_date", "visit_time"],
    }
    required = required_by_stage.get(stage, [])
    return [slot for slot in required if not str(known_slots.get(slot) or "").strip()]


def _ask_policy(stage: str, content: str, known_slots: dict[str, Any], missing_slots: list[str]) -> str:
    if stage in {"answer_only", "handoff_at_store", "service_recovery"}:
        return "no_ask"
    if _rejects_questions(content):
        return "no_ask"
    city = _extract_city_from_content(content)
    narrow_fee_question = _is_narrow_fee_question(content)
    narrow_store_request = _is_narrow_store_request(content)
    if stage == "store_paving" and (city or narrow_store_request) and not missing_slots:
        return "no_ask"
    if stage == "price_paving" and (_has_clear_need_or_direction(content) or known_slots.get("need") or known_slots.get("project_direction")):
        return "no_ask"
    if stage == "quote" and narrow_fee_question:
        return "no_ask"
    if stage == "opening_intro" and _needs_opening_question(content):
        return "ask_one"
    if stage == "collect_info" and missing_slots:
        return "collect_required"
    if missing_slots:
        return "ask_one"
    return "no_ask"


def _next_best_action(stage: str, known_slots: dict[str, Any], missing_slots: list[str]) -> str:
    if stage == "opening_intro":
        if known_slots.get("need") or known_slots.get("project_direction"):
            return "短承接客户需求，直接给改善方向；如果已有活动口径，就顺带给活动/价格铺垫，不重新追问已知问题。"
        return "短承接客户需求，给一个方向或只问一个最关键问题。"
    if stage == "store_paving":
        if known_slots.get("city"):
            return "直接推荐最近或最方便门店；能给地址、导航、停车就一次发全，不再反问客户选哪家。"
        return "只补问城市或所在区域。"
    if stage == "price_paving":
        if known_slots.get("need") or known_slots.get("project_direction"):
            return "先给项目方向或活动方向，再补一句收费口径，让客户形成价格预期，不主动追问。"
        return "先解释项目/活动价值和收费口径，再自然过渡到报价。"
    if stage == "quote":
        return "先回答当前价格口径；只有客户明确想来时，才轻提预约登记。"
    if stage == "close_order":
        if missing_slots:
            return f"客户意向已较强，只补一个关键槽位：{missing_slots[0]}，不要连问。"
        return "客户意向已较强，轻推登记活动名额或确认到店时间，不提前收姓名电话。"
    if stage == "collect_info":
        if missing_slots:
            return f"只收集缺失预约信息：{missing_slots[0]}，优先门店、日期、时间，再到姓名电话。"
        return "复述预约开单信息，请客户确认后开预约入口和10元预约金小程序。"
    if stage == "handoff_at_store":
        return "客户已到店，交给门店专业同事接待。"
    if stage == "service_recovery":
        return "先承接不满或风险，再让专业同事结合记录处理。"
    return "只回答当前问题，收住不发散。"


def _push_goal(stage: str) -> str:
    if stage in {"opening_intro", "price_paving"}:
        return "引导客户形成清晰项目/活动兴趣。"
    if stage == "store_paving":
        return "降低到店成本，让客户知道去哪家更方便。"
    if stage in {"quote", "close_order"}:
        return "让客户有价格预期，并推进预约登记。"
    if stage == "collect_info":
        return "确认预约开单必要信息。"
    if stage == "handoff_at_store":
        return "让门店人员及时承接。"
    return "先解决当前问题。"


def _reply_rhythm(stage: str, ask_policy: str) -> str:
    if ask_policy == "no_ask":
        return "优先1条消息，直接给结论和必要说明，不用问句收尾。"
    if ask_policy == "collect_required":
        return "先复述已知预约信息，再只问一个缺失信息。"
    if stage in {"store_paving", "quote", "close_order"}:
        return "默认1条答核心；只有地址资料和下一步明显不同才拆第2条。"
    return "短承接后最多问一个关键问题；已知信息足够时不追问。"


def _has_arrival_intent(content: str) -> bool:
    return any(
        term in content
        for term in [
            "想来",
            "想过去",
            "能去吗",
            "可以去吗",
            "过去看看",
            "到店",
            "安排接待",
            "今天能来",
            "下午来",
            "周末来",
            "约一下",
        ]
    )


def _has_confirmed_booking_signal(content: str) -> bool:
    return any(
        term in content
        for term in [
            "姓名",
            "电话",
            "手机号",
            "我叫",
            "这是我电话",
            "发你电话",
            "发你名字",
            "帮我登记",
            "开预约",
            "发小程序",
            "发付款码",
            "付10元",
            "付预约金",
        ]
    )


def _stage_reason(stage: str, intents: set[str], known_slots: dict[str, Any]) -> str:
    details = []
    if intents:
        details.append("本轮意图：" + "、".join(sorted(intent for intent in intents if intent)))
    known = [key for key, value in known_slots.items() if str(value or "").strip()]
    if known:
        details.append("已知信息：" + "、".join(known[:5]))
    return "；".join(details) or f"当前按{SALES_STAGE_LABELS.get(stage, stage)}承接。"


def _compact_slots(slots: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in slots.items() if str(value or "").strip()}


def _extract_city_from_content(content: str) -> str:
    if "浦东机场" in content or "虹桥机场" in content:
        return "上海"
    if "高崎机场" in content:
        return "厦门"
    for name in CITY_NAMES:
        if name and name in content:
            return name
    for area, mapped_city in AREA_CITY_MAP.items():
        if area and area in content:
            return mapped_city
    city = extract_city(content)
    if city in CITY_NAMES:
        return city
    return ""


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    return str(value or "").strip() if not isinstance(value, dict) else ""


def _known_info_contains(intents: list[dict[str, Any]], terms: list[str]) -> str:
    for item in intents:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(part or "") for part in item.get("known_info") or [])
        for term in terms:
            if term in text:
                return term
    return ""


def _has_opening_ready_slots(known: dict[str, Any]) -> bool:
    return all(str(known.get(key) or "").strip() for key in ["store_name", "visit_date_value", "visit_time"])


def _has_customer_confirmation(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if "?" in text or "？" in text:
        return False
    if any(term in text for term in ["可以去吗", "能去吗", "能不能", "想过来看看", "过来看看"]):
        return False
    return any(term in text for term in ["确认", "好的", "行", "就这个", "就这家", "约", "开单", "发我", "可以"])


def _is_at_store(content: str) -> bool:
    return any(term in content for term in ["我到了", "到店了", "在前台", "已经到店", "到你们店了", "我在店里"])


def _is_early_intro(content: str) -> bool:
    return len(content) <= 16 or any(term in content for term in ["了解一下", "咨询一下", "想看看", "有什么项目"])


def _needs_opening_question(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return True
    if text in {"你好", "您好", "在吗", "哈喽", "hello", "hi"}:
        return True
    if any(term in text for term in ["了解一下", "咨询一下", "想看看", "有什么项目", "介绍一下"]):
        return True
    return False


def _rejects_questions(content: str) -> bool:
    return any(term in content for term in ["别问了", "不要问", "直接说", "你判断", "你推荐", "我不懂"])


def _is_narrow_fee_question(content: str) -> bool:
    return any(
        term in content
        for term in [
            "一次费用",
            "一次的费用",
            "确定",
            "268",
            "199",
            "380",
            "多少钱",
            "价格",
            "费用",
            "有没有其他收费",
            "另收费",
            "加钱",
            "全款",
            "定金",
            "预约金",
            "尾款",
        ]
    )


def _is_narrow_store_request(content: str) -> bool:
    return any(
        term in content
        for term in [
            "地址",
            "导航",
            "停车",
            "路线",
            "发我",
            "哪家近",
            "机场附近",
            "高铁站附近",
            "最近的店",
        ]
    )


def _has_clear_need_or_direction(content: str) -> bool:
    return any(
        term in content
        for term in [
            "祛斑",
            "淡斑",
            "毛孔",
            "暗沉",
            "抗衰",
            "补水",
            "肤色不均",
            "色沉",
            "点状斑",
            "松弛",
            "提升",
        ]
    )
