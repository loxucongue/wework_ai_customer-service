from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SopPlatformScene:
    code: str
    name: str
    model_selectable: bool
    decision: str


_BUSINESS_SCENES = (
    ("objection_distance_local_store_far", "距离异议｜本地有店但距离远", "send"),
    ("objection_distance_cross_city", "距离异议｜本地无店需跨城", "send"),
    ("objection_effect_guaranteed_clear", "效果异议｜要求承诺完全去除", "send"),
    ("objection_effect_recovery_discomfort", "效果异议｜疼痛红肿或恢复期", "send"),
    ("objection_effect_side_effect", "效果异议｜担心副作用", "send"),
    ("objection_effect_one_session", "效果异议｜担心一次效果", "send"),
    ("objection_effect_case_similarity", "效果异议｜担心与案例效果不一致", "send"),
    ("objection_effect_recurrence", "效果异议｜担心反弹或再长", "send"),
    ("objection_price_hidden_charge", "价格异议｜担心隐形消费", "send"),
    ("objection_price_discount_request", "价格异议｜希望更低价格", "send"),
    ("objection_price_competitor_cheaper", "价格异议｜对比其他家价格", "send"),
    ("objection_price_refund_guarantee", "价格异议｜担心无效退款", "send"),
    ("objection_time_unavailable", "时间异议｜暂时没有时间", "send"),
    ("normal_platform_intent", "正常推进｜平台任务内容", "send"),
    ("normal_light_effect", "正常推进｜轻触达效果展示", "send"),
    ("normal_activity_price", "正常推进｜活动价格", "send"),
    ("no_send_complaint_or_refund", "不发送｜严重客诉或退款纠纷", "no_send"),
    ("no_send_explicit_stop_contact", "不发送｜明确停止联系", "no_send"),
    ("no_send_customer_deleted", "不发送｜客户关系删除", "no_send"),
    ("no_send_health_risk", "不发送｜健康风险", "no_send"),
    ("no_send_paid_or_appointment_conflict", "不发送｜已付或已预约冲突", "no_send"),
    ("no_send_human_takeover", "不发送｜人工正在接待", "no_send"),
    ("no_send_platform_content_conflict", "不发送｜平台内容冲突", "no_send"),
)

_TECHNICAL_SCENES = (
    ("ai_service_unopened_passthrough", "平台原文｜客户未开口", "send"),
    ("no_send_invalid_message_content", "不发送｜平台内容无效", "no_send"),
    ("quiet_first_add_backlog", "夜间拦截｜次日08:30融合", "no_send"),
    ("no_send_duplicate", "不发送｜重复内容", "no_send"),
    ("no_send_downstream_rejected", "不发送｜下游发送拒绝", "no_send"),
)

SOP_PLATFORM_SCENES = {
    code: SopPlatformScene(
        code=code,
        name=name,
        model_selectable=model_selectable,
        decision=decision,
    )
    for model_selectable, rows in ((True, _BUSINESS_SCENES), (False, _TECHNICAL_SCENES))
    for code, name, decision in rows
}

SOP_PLATFORM_MODEL_SCENE_CODES = frozenset(
    code for code, scene in SOP_PLATFORM_SCENES.items() if scene.model_selectable
)
SOP_PLATFORM_TECHNICAL_SCENE_CODES = frozenset(
    code for code, scene in SOP_PLATFORM_SCENES.items() if not scene.model_selectable
)

# Knowledge 9 is mislabeled upstream as recurrence, but its paragraphs are all
# about transparent pricing. Keep semantic routing tied to content, not its name.
SOP_PLATFORM_KNOWLEDGE_SCENE_CODES = {
    1: "objection_distance_local_store_far",
    2: "objection_distance_cross_city",
    3: "objection_effect_guaranteed_clear",
    4: "objection_effect_recovery_discomfort",
    5: "objection_effect_side_effect",
    6: "objection_effect_one_session",
    7: "objection_effect_case_similarity",
    8: "objection_effect_recurrence",
    9: "objection_price_hidden_charge",
    10: "objection_price_hidden_charge",
    11: "objection_price_discount_request",
    12: "objection_price_competitor_cheaper",
    13: "objection_price_refund_guarantee",
    14: "objection_time_unavailable",
}


def sop_platform_scene(code: str) -> SopPlatformScene | None:
    return SOP_PLATFORM_SCENES.get(str(code or "").strip())


def sop_platform_scene_name(code: str) -> str:
    scene = sop_platform_scene(code)
    return scene.name if scene is not None else ""


def sop_platform_model_scene_catalog() -> list[dict[str, str]]:
    return [
        {"sceneCode": scene.code, "sceneName": scene.name, "decision": scene.decision}
        for scene in SOP_PLATFORM_SCENES.values()
        if scene.model_selectable
    ]

def sop_platform_knowledge_scene_catalog() -> list[dict[str, str | int]]:
    return [
        {
            "knowledgeId": knowledge_id,
            "sceneCode": scene_code,
            "sceneName": sop_platform_scene_name(scene_code),
        }
        for knowledge_id, scene_code in SOP_PLATFORM_KNOWLEDGE_SCENE_CODES.items()
    ]
