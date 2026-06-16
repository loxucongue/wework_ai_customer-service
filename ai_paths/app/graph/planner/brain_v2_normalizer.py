from __future__ import annotations

from typing import Any

from app.graph.planner.tool_policy import (
    dedupe_tools,
    enforce_required_tools,
    needs_appointment_time_request,
    needs_store_lookup_request,
    normalize_tools,
    tool_policy_violations,
)
from app.graph.signals.project import has_case_request
from app.graph.state import AgentState
from app.policies.sop_rules import normalize_sop_stage, normalize_sop_step


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    primary_raw = model_payload.get("primary_task") if isinstance(model_payload, dict) else {}
    secondary_raw = model_payload.get("secondary_tasks") if isinstance(model_payload, dict) else []
    reply_strategy_raw = model_payload.get("reply_strategy") if isinstance(model_payload, dict) else {}
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    primary_task = _normalize_task(primary_raw, default_priority=1)
    secondary_tasks = [
        _normalize_task(item, default_priority=index + 2)
        for index, item in enumerate(secondary_raw if isinstance(secondary_raw, list) else [])
        if isinstance(item, dict)
    ]
    secondary_tasks = [item for item in secondary_tasks if item][:2]

    if not primary_task:
        raise ValueError("Planner Brain missing valid primary_task")

    all_tasks = [primary_task, *secondary_tasks]
    normalized_content = str(state.get("normalized_content") or "")
    _coerce_primary_task_from_text_signals(primary_task, normalized_content)
    if needs_appointment_time_request(normalized_content):
        _coerce_primary_task_to_appointment_time(primary_task, reply_strategy_raw)
    elif needs_store_lookup_request(state, normalized_content) and _looks_like_location_or_store_question(normalized_content):
        _coerce_primary_task_to_store_lookup(primary_task, reply_strategy_raw)
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    request_stage = str(request_context.get("customer_stage") or "").strip()
    sop_stage = normalize_sop_stage(
        primary_task.get("sop_stage") or model_payload.get("sop_stage"),
        task_type=str(primary_task.get("type") or ""),
        request_stage=request_stage,
    )
    sop_step = normalize_sop_step(
        sop_stage,
        model_payload.get("sop_step") or primary_task.get("sop_step") or primary_task.get("scene") or primary_task.get("subtype"),
    )
    primary_task["sop_stage"] = sop_stage
    primary_task["sop_step"] = sop_step
    reply_strategy = _normalize_reply_strategy(reply_strategy_raw, all_tasks)
    handoff = _normalize_handoff(handoff_raw, primary_task, secondary_tasks)
    required_tools = dedupe_tools([tool for task in all_tasks for tool in task.get("tools", [])])
    required_tools = enforce_required_tools(state, all_tasks, required_tools)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    violations = tool_policy_violations(all_tasks, required_tools)
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
        "sop_stage": sop_stage,
        "sop_step": sop_step,
    }


def safety_fallback_plan(state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    primary_task = {
        "type": "human_request",
        "subtype": "planner_unavailable_or_guardrail",
        "policy_hint": "HUMAN_HANDOFF_PROFESSIONAL_ASSIST",
        "scene": "S7_dealed_active",
        "subflow": "HUMAN_HANDOFF",
        "sop_stage": "S4_FOLLOWUP_REACTIVATE",
        "sop_step": "已成交售后",
        "customer_need": content[:120] or "Needs a professional colleague to continue handling",
        "answer_goal": "Acknowledge the customer's current message and arrange professional assistance without making up facts",
        "priority": 1,
        "known_info": [],
        "missing_info": [],
        "must_answer": ["Current user question"],
        "must_avoid": ["Made-up facts", "Guaranteed results", "Code-side business judgment"],
        "should_ask": False,
        "tools": [{"name": "professional_assist", "purpose": "Guardrail required professional follow-up"}],
    }
    return build_planner_plan_v2(
        state,
        {
            "primary_task": primary_task,
            "secondary_tasks": [],
            "reply_strategy": {
                "tone": "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d",
                "must_answer": ["Current user question"],
                "can_push": "",
                "must_avoid": ["Made-up facts", "Guaranteed results", "Internal process exposure"],
                "max_questions": 0,
            },
            "handoff": {"needed": True, "reason": "Planner unavailable or hard guardrail requires professional assistance"},
            "memory_update_hint": {},
        },
    )


def _coerce_primary_task_to_store_lookup(primary_task: dict[str, Any], reply_strategy_raw: Any) -> None:
    primary_task["type"] = "store_inquiry"
    primary_task["subtype"] = primary_task.get("subtype") or "nearest_store"
    primary_task["policy_hint"] = primary_task.get("policy_hint") or "SF6_STORE_NEAREST"
    primary_task["scene"] = primary_task.get("scene") or "S2 门店地址"
    primary_task["subflow"] = primary_task.get("subflow") or "STORE_LOOKUP"
    primary_task["sop_stage"] = "S2_STORE_ADDRESS"
    primary_task["sop_step"] = primary_task.get("sop_step") or "询问地址"
    primary_task["answer_goal"] = (
        primary_task.get("answer_goal")
        or "Use real store facts to answer the customer's location or nearby-store question."
    )
    primary_task["must_answer"] = list(primary_task.get("must_answer") or []) or [
        "根据真实门店事实回答附近或更方便的门店"
    ]
    primary_task["must_avoid"] = list(primary_task.get("must_avoid") or []) + [
        "没有门店事实时编造地址、营业时间或距离"
    ]
    if isinstance(reply_strategy_raw, dict):
        reply_strategy_raw.setdefault("can_push", "帮客户确认更近门店或下一步到店时间")


def _coerce_primary_task_to_appointment_time(primary_task: dict[str, Any], reply_strategy_raw: Any) -> None:
    primary_task["type"] = "appointment"
    primary_task["subtype"] = primary_task.get("subtype") or "time_check"
    primary_task["policy_hint"] = primary_task.get("policy_hint") or "SF9_APPOINTMENT_TIME_CHECK"
    primary_task["scene"] = primary_task.get("scene") or "S3 报价收单"
    primary_task["subflow"] = primary_task.get("subflow") or "APPOINTMENT_TIME_CHECK"
    primary_task["sop_stage"] = "S3_PRICE_CLOSE"
    primary_task["sop_step"] = primary_task.get("sop_step") or "收单"
    primary_task["answer_goal"] = (
        primary_task.get("answer_goal")
        or "Check real store and availability facts before answering whether the customer can visit at that time."
    )
    primary_task["must_answer"] = list(primary_task.get("must_answer") or []) or [
        "根据真实门店和档期事实回答客户能不能约这个时间"
    ]
    primary_task["must_avoid"] = list(primary_task.get("must_avoid") or []) + [
        "没有真实档期时说预约成功或确认可约"
    ]
    if isinstance(reply_strategy_raw, dict):
        reply_strategy_raw.setdefault("can_push", "确认门店、时间或补齐预约信息")


def _coerce_primary_task_from_text_signals(primary_task: dict[str, Any], content: str) -> None:
    text = str(content or "").strip()
    current_type = str(primary_task.get("type") or "").strip()
    if not text:
        primary_task["type"] = _normalize_task_type_alias(current_type)
        return
    if _looks_like_complaint_or_refund(text):
        primary_task["type"] = "complaint_refund"
        primary_task.setdefault("policy_hint", "HUMAN_HANDOFF_COMPLAINT_REFUND")
        primary_task.setdefault("subflow", "HUMAN_HANDOFF")
        primary_task["sop_stage"] = "S4_FOLLOWUP_REACTIVATE"
        return
    if _looks_like_after_sales_feedback(text):
        primary_task["type"] = "after_sales"
        primary_task.setdefault("policy_hint", "SF12_AFTER_SALES_EFFECT_FEEDBACK")
        primary_task["sop_stage"] = "S4_FOLLOWUP_REACTIVATE"
        return
    if has_case_request(text):
        primary_task["type"] = "case_request"
        primary_task.setdefault("policy_hint", "CASE_EFFECT_REFERENCE_SAFE")
        primary_task["sop_stage"] = "S3_PRICE_CLOSE"
        return
    if _looks_like_booking_intent(text):
        primary_task["type"] = "appointment"
        primary_task.setdefault("policy_hint", "SF9_APPOINTMENT_BOOKING_INTENT")
        primary_task["sop_stage"] = "S3_PRICE_CLOSE"
        return
    if _looks_like_price_or_fee_concern(text):
        primary_task["type"] = "price_inquiry"
        primary_task.setdefault("policy_hint", "SF7_PRICE_ACTIVITY")
        primary_task["sop_stage"] = "S3_PRICE_CLOSE"
        return
    if _looks_like_trust_question(text):
        primary_task["type"] = "trust_issue"
        primary_task.setdefault("policy_hint", "SF10_TRUST_BUILD")
        return
    if _looks_like_project_question(text):
        primary_task["type"] = "project_consult"
        primary_task.setdefault("policy_hint", "SF3_PROJECT_CONSULT")
        return
    primary_task["type"] = _normalize_task_type_alias(current_type)


def _normalize_task_type_alias(task_type: str) -> str:
    text = str(task_type or "").strip()
    aliases = {
        "greeting": "opening",
        "greeting_activation": "opening",
        "greeting_and_activation": "opening",
        "opening_greeting": "opening",
        "承接": "opening",
        "inquiry": "project_consult",
        "general_inquiry": "project_consult",
        "method_inquiry": "project_consult",
        "project_inquiry": "project_consult",
        "question": "project_consult",
        "effect_showcase": "case_request",
        "效果案例": "case_request",
        "trust_building": "trust_issue",
        "fee_transparency": "price_inquiry",
        "deposit_concern": "price_inquiry",
    }
    return aliases.get(text, text)


def _looks_like_location_or_store_question(text: str) -> bool:
    return any(
        term in text
        for term in (
            "我在",
            "我住",
            "附近",
            "机场",
            "高铁",
            "地铁",
            "科技园",
            "哪个区",
            "哪家",
            "门店",
            "地址",
            "位置",
            "导航",
            "停车",
            "营业时间",
        )
    )


def _looks_like_price_or_fee_concern(text: str) -> bool:
    return any(
        term in text
        for term in (
            "多少钱",
            "价格",
            "费用",
            "报价",
            "活动",
            "优惠",
            "199",
            "268",
            "308",
            "58",
            "380",
            "乱收费",
            "隐形消费",
            "加价",
            "推销",
            "定金",
            "订金",
            "预约金",
            "尾款",
            "全款",
            "不交",
            "到店再付",
        )
    )


def _looks_like_booking_intent(text: str) -> bool:
    positive_terms = ("帮我登记", "给我登记", "预约一个", "报名", "先交10", "交10元", "付10元", "支付10元")
    negative_terms = ("不交", "不付", "到店再付", "退", "退款", "投诉")
    return any(term in text for term in positive_terms) and not any(term in text for term in negative_terms)


def _looks_like_after_sales_feedback(text: str) -> bool:
    return any(term in text for term in ("不见效果", "没效果", "没有效果", "一点效果", "做了2次", "做了两次", "已做"))


def _looks_like_complaint_or_refund(text: str) -> bool:
    return any(term in text for term in ("投诉", "退款", "退钱", "骗", "多收", "乱收费")) and any(
        term in text for term in ("退", "投诉", "处理", "骗", "多收")
    )


def _looks_like_trust_question(text: str) -> bool:
    return any(term in text for term in ("资质", "正规吗", "靠谱吗", "是不是门店", "你是门店", "营业执照"))


def _looks_like_project_question(text: str) -> bool:
    return any(
        term in text
        for term in (
            "淡斑",
            "祛斑",
            "斑",
            "黑色素",
            "敏感皮",
            "敏感肌",
            "方法",
            "可以做",
            "能做",
            "痣",
            "痦子",
            "伤皮肤",
        )
    )


def _normalize_task(raw: Any, *, default_priority: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        priority = int(raw.get("priority", default_priority))
    except (TypeError, ValueError):
        priority = default_priority
    tools = dedupe_tools(normalize_tools(raw.get("tools") or []))
    return {
        "type": str(raw.get("type") or "").strip(),
        "subtype": str(raw.get("subtype") or "").strip(),
        "policy_hint": str(raw.get("policy_hint") or "").strip(),
        "scene": str(raw.get("scene") or "").strip(),
        "subflow": str(raw.get("subflow") or "").strip(),
        "sop_stage": str(raw.get("sop_stage") or "").strip(),
        "sop_step": str(raw.get("sop_step") or "").strip(),
        "customer_need": str(raw.get("customer_need") or "").strip(),
        "answer_goal": str(raw.get("answer_goal") or "").strip(),
        "priority": priority,
        "known_info": _clean_str_list(raw.get("known_info") or []),
        "missing_info": _clean_str_list(raw.get("missing_info") or []),
        "must_answer": _clean_str_list(raw.get("must_answer") or []),
        "must_avoid": _clean_str_list(raw.get("must_avoid") or []),
        "should_ask": bool(raw.get("should_ask")),
        "tools": tools or [{"name": "no_tool", "purpose": "This turn can be acknowledged directly"}],
    }


def _normalize_reply_strategy(raw: Any, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    primary = tasks[0] if tasks else {}
    tone = str(raw.get("tone") or "").strip() or "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d"
    must_answer = _clean_str_list(raw.get("must_answer") or []) or list(primary.get("must_answer") or [])
    can_push = str(raw.get("can_push") or "").strip()
    must_avoid = _clean_str_list(raw.get("must_avoid") or []) or list(primary.get("must_avoid") or [])
    try:
        max_questions = int(raw.get("max_questions", 1))
    except (TypeError, ValueError):
        max_questions = 1
    return {
        "tone": tone[:120],
        "must_answer": must_answer[:8],
        "can_push": can_push[:180],
        "must_avoid": must_avoid[:8],
        "max_questions": 0 if max_questions < 0 else min(max_questions, 2),
    }


def _normalize_handoff(raw: Any, primary_task: dict[str, Any], secondary_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    if str(primary_task.get("type") or "").strip() in {"human_request", "complaint_refund"}:
        needed = True
    if any(str(item.get("type") or "").strip() in {"human_request", "complaint_refund"} for item in secondary_tasks):
        needed = True
    return {
        "needed": needed,
        "reason": str(raw.get("reason") or "").strip()[:180],
    }


def _normalize_memory_hint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "summary": str(raw.get("summary") or "").strip()[:180],
        "needs": _clean_str_list(raw.get("needs") or [])[:6],
        "concerns": _clean_str_list(raw.get("concerns") or [])[:6],
        "store_preference": str(raw.get("store_preference") or "").strip()[:80],
        "appointment_signals": _clean_str_list(raw.get("appointment_signals") or [])[:6],
    }


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text[:180])
    return output
