from __future__ import annotations

from typing import Any


HEALTH_RISK_TERMS = (
    "心脏病",
    "高血压",
    "糖尿病",
    "怀孕",
    "孕期",
    "哺乳",
    "未成年",
    "过敏",
    "严重过敏",
    "过敏体质",
    "过敏史",
    "脸肿",
    "面部肿",
    "病史",
    "慢病",
    "用药",
    "处方",
    "病历",
    "病例",
    "医学报告",
)
SEVERE_DISCOMFORT_TERMS = ("严重不适", "红肿", "刺痛", "流脓", "发烧", "烂脸", "脸疼", "脸痛")
DISPUTE_TERMS = (
    "退款",
    "退钱",
    "投诉",
    "维权",
    "报警",
    "曝光",
    "付款异常",
    "支付异常",
    "扣款",
    "多收",
    "订单纠纷",
    "骗钱",
)
HUMAN_REQUEST_TERMS = ("人工", "真人", "换人", "机器人")
RELEASE_TERMS = ("检测完", "已经检测", "确认适合", "门店说可以", "老师说可以", "评估可以")


def explicit_professional_assist_reason(state: dict[str, Any]) -> str:
    text = _current_and_merged_text(state)
    if not text:
        return ""
    if _contains_any(text, HEALTH_RISK_TERMS):
        return "健康高风险：需到店检测后确认适配性"
    if _contains_any(text, SEVERE_DISCOMFORT_TERMS):
        return "严重不适：需先核对门店、时间、项目和当前状态"
    if _contains_any(text, DISPUTE_TERMS):
        return "投诉退款或付款纠纷：需核对门店、付款时间、金额和项目"
    if _contains_any(text, HUMAN_REQUEST_TERMS):
        return "客户明确要求真人处理"
    return ""


def health_risk_hold(state: dict[str, Any]) -> dict[str, Any]:
    current = _current_and_merged_text(state)
    if _contains_any(current, RELEASE_TERMS):
        return {}
    if _contains_any(current, HEALTH_RISK_TERMS):
        return {
            "risk_hold": "health_check_required",
            "severity": "hard",
            "source": "current_message",
            "reason": "当前消息包含健康/过敏高风险，需先到店检测确认适配性",
        }
    recent = _recent_history_text(state)
    if _contains_any(recent, HEALTH_RISK_TERMS) or (
        "human_handoff_notice" in recent and _contains_any(recent, ("健康", "适配", "心脏", "血压", "过敏"))
    ):
        return {
            "risk_hold": "health_check_context",
            "severity": "advisory",
            "source": "recent_history",
            "reason": "近期出现过健康/过敏风险，本轮正常承接客户当前问题时顺带提醒到店先检测确认适配性",
        }
    return {}


def is_hard_health_risk_hold(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return str(value.get("risk_hold") or "") == "health_check_required" or str(value.get("severity") or "") == "hard"


def current_health_risk_hold_for_model(state: dict[str, Any]) -> dict[str, Any]:
    """Expose only a current hard-risk safety fact to business models.

    Recent resolved health history remains in conversation_history for model
    interpretation. Passing an advisory workflow conclusion here would give old
    risk more authority than the customer's current question.
    """

    value = health_risk_hold(state)
    return value if is_hard_health_risk_hold(value) else {}


def _current_and_merged_text(state: dict[str, Any]) -> str:
    parts = [
        str(state.get("normalized_content") or state.get("content") or ""),
    ]
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    merged = request_context.get("merged_customer_messages")
    if isinstance(merged, list):
        parts.extend(str(item or "") for item in merged)
    return "\n".join(part for part in parts if part)


def _recent_history_text(state: dict[str, Any]) -> str:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    return "\n".join(str(item or "") for item in history[-3:])


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
