from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.chat_runtime import ChatRuntime
from app.graph.nodes.conversation_history_fetch import newer_customer_message_after_trigger
from app.graph.nodes.reply_delivery_manifest import (
    authorize_sop_delivery_manifest,
    build_sop_delivery_manifest,
    merge_manifest_into_reply_contract,
)
from app.graph.nodes.reply_quality import collect_reply_soft_warnings
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT
from app.prompts.reply_synthesizer import REPLY_SYSTEM_PROMPT
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyRecord


ACTIVITY_TEXT = (
    "现在是线上抢购活动，前30名顾客到店可以享受268元淡斑特惠价。"
    "套餐包括淡斑、检测皮肤、基础清洁和肌肤补水。"
    "线上预定每位10元，到店抵扣10元，做的话再付258元；"
    "未做或不满意可退，实际按付款记录核对。"
    "活动仅限线上报名，名额满活动结束；预定后到店时间按您方便安排。"
)
ACTIVITY_IMAGE = "https://oss.example.test/activity.png"
CASE_IMAGE = "https://oss.example.test/case-001.png"


def test_adjacent_payment_card_prompt_contract_is_consistent() -> None:
    assert "相邻上一轮已经发送预约金卡时，本轮严禁再次发卡" in GLOBAL_BUSINESS_RHYTHM_CONTRACT
    assert "不得连续发送预约金卡" in GLOBAL_BUSINESS_RHYTHM_CONTRACT
    assert "上一轮已经发卡时只能文字推动客户操作已有卡或选择转账" in REPLY_SYSTEM_PROMPT
    assert "同轮重发卡" not in GLOBAL_BUSINESS_RHYTHM_CONTRACT


def _activity_gate() -> dict:
    return {
        "route": "ai_then_sop",
        "send_sop": True,
        "sop_pack_id": "s10_activity_intro",
        "reply_messages": [
            {"type": "text", "order": 1, "content": {"text": ACTIVITY_TEXT}},
            {"type": "image", "order": 2, "content": {"url": ACTIVITY_IMAGE}},
            {
                "type": "text",
                "order": 3,
                "content": {"text": "到店先看效果和方案，满意再做，活动时间按您方便安排。"},
            },
            {"type": "payment_collection", "order": 4, "content": {"amount": 10}},
        ],
    }


def _effect_state(scene_id: str = "effect_definition_trust") -> dict:
    return {
        "content": "不是去斑吗？",
        "normalized_content": "不是去斑吗？",
        "sop_gate_decision": {"selected_scene_id": scene_id, "route": "ai_only"},
        "sop_delivery_manifest": build_sop_delivery_manifest(_activity_gate()),
        "conversation_history": ["小贝: 现在活动价268元，先交10元就可以登记。", "用户: 不是去斑吗？"],
        "history_events": [],
    }


def _effect_model_payload(scene_id: str = "effect_definition_trust") -> dict:
    return {
        "decision": "direct_reply",
        "stage": "S2",
        "sub_rule_id": "S2_EFFECT",
        "conversion_stage": "interest_capture",
        "customer_type": "project",
        "main_blocker": "none",
        "next_step": "introduce_offer",
        "precision_qa_decision": {"question_id": scene_id, "confidence": "high"},
        "payment_decision": {"action": "send_now", "amount": 10},
        "sales_progression": {
            "status": "continue",
            "target_stage": "deposit",
            "action": "send_payment_card",
            "required_message_types": ["text", "payment_collection"],
        },
        "reply_messages": [
            {"type": "text", "content": "活动价268元，先付10元。"},
            {"type": "payment_collection", "content": {"amount": 10}},
        ],
        "tool_calls": [],
    }


def _effect_reply_state(plan: dict) -> dict:
    return {
        **plan,
        "fact_envelope": {
            "structured_facts": {
                "case_facts": [
                    {
                        "document_id": "case-001",
                        "image_url": CASE_IMAGE,
                        "title": "真实改善参考",
                    }
                ]
            }
        },
    }


def _valid_effect_messages() -> list[dict]:
    return [
        {
            "type": "text",
            "order": 1,
            "content": "亲，我们是做斑点改善的，绝大多数顾客一次就有很好的改善效果，我给您看同类型的真实参考。",
        },
        {
            "type": "text",
            "order": 2,
            "content": "完成线上活动登记后，您可以到门店免费做皮肤检测，门店会结合您的具体情况给您讲清楚。",
        },
        {"type": "image", "order": 3, "content": {"url": CASE_IMAGE}},
    ]


def test_activity_manifest_keeps_full_sequence_but_drops_unauthorized_card() -> None:
    manifest = build_sop_delivery_manifest(_activity_gate())
    authorized = authorize_sop_delivery_manifest(
        manifest,
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "deliver_now", "sop_pack_id": "s10_activity_intro"},
    )
    contract = merge_manifest_into_reply_contract({}, authorized)
    messages = _activity_gate()["reply_messages"][:-1]

    assert [item["message_type"] for item in authorized["messages"]] == ["text", "image", "text"]
    assert authorized["suppressed_messages"][0]["suppressed_reason"] == "planner_payment_not_authorized"
    validate_reply_consistency(
        messages,
        {"authorized_sop_delivery_manifest": authorized, "reply_contract": contract},
    )


@pytest.mark.parametrize(
    "redemption_text",
    [
        "10元预约金到了门店直接抵扣",
        "10元预约金到门店后可以直接抵扣",
        "10元预约金到店时可抵扣",
        "到店后做的话再付258元，10元会直接抵扣",
    ],
)
def test_activity_manifest_accepts_natural_redemption_wording(redemption_text: str) -> None:
    authorized = authorize_sop_delivery_manifest(
        build_sop_delivery_manifest(_activity_gate()),
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "deliver_now", "sop_pack_id": "s10_activity_intro"},
    )
    contract = merge_manifest_into_reply_contract({}, authorized)
    messages = _activity_gate()["reply_messages"][:-1]
    messages[0] = {
        **messages[0],
        "content": {
            "text": ACTIVITY_TEXT.replace("到店抵扣10元", redemption_text),
        },
    }

    validate_reply_consistency(
        messages,
        {"authorized_sop_delivery_manifest": authorized, "reply_contract": contract},
    )


def test_activity_manifest_replaces_duplicate_model_pack_requirements() -> None:
    authorized = authorize_sop_delivery_manifest(
        build_sop_delivery_manifest(_activity_gate()),
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "deliver_now", "sop_pack_id": "s10_activity_intro"},
    )
    contract = merge_manifest_into_reply_contract(
        {
            "required_deliveries": [
                {"message_type": "text", "source_pack_id": "s10_activity_intro"},
                {"message_type": "image", "source_pack_id": "s10_activity_intro"},
                {"message_type": "text", "source_pack_id": "s10_activity_intro"},
            ]
        },
        authorized,
    )

    assert [item["message_type"] for item in contract["required_deliveries"]] == ["text", "image", "text"]


def test_activity_manifest_blocks_compressed_or_incomplete_activity_copy() -> None:
    manifest = authorize_sop_delivery_manifest(
        build_sop_delivery_manifest(_activity_gate()),
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "deliver_now", "sop_pack_id": "s10_activity_intro"},
    )
    contract = merge_manifest_into_reply_contract({}, manifest)
    compressed = [
        {"type": "text", "order": 1, "content": "亲，现在活动是268元，您可以先了解一下。"},
        {"type": "image", "order": 2, "content": {"url": ACTIVITY_IMAGE}},
        {"type": "text", "order": 3, "content": "我接着给您说清楚。"},
    ]

    with pytest.raises(ValueError, match="activity_intro_core_facts_missing|empty_delivery_promise_not_allowed"):
        validate_reply_consistency(
            compressed,
            {"authorized_sop_delivery_manifest": manifest, "reply_contract": contract},
        )


def test_ai_then_sop_remains_candidate_until_planner_delivers_it() -> None:
    manifest = build_sop_delivery_manifest(_activity_gate())

    deferred = authorize_sop_delivery_manifest(
        manifest,
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "defer", "sop_pack_id": "s10_activity_intro"},
    )

    assert deferred["active"] is False
    assert deferred["reason"] == "planner_defer"
    assert merge_manifest_into_reply_contract({}, deferred)["required_deliveries"] == []


def test_ai_then_sop_without_planner_decision_defaults_to_defer() -> None:
    deferred = authorize_sop_delivery_manifest(
        build_sop_delivery_manifest(_activity_gate()),
        payment_decision={"action": "none"},
        precision_scene_id="",
    )

    assert deferred["active"] is False
    assert deferred["delivery_decision"]["action"] == "defer"


def test_sop_only_keeps_gate_direct_delivery_when_planner_field_is_missing() -> None:
    gate = {**_activity_gate(), "route": "sop_only"}
    authorized = authorize_sop_delivery_manifest(
        build_sop_delivery_manifest(gate),
        payment_decision={"action": "none"},
        precision_scene_id="",
    )

    assert authorized["active"] is True
    assert authorized["delivery_decision"]["action"] == "deliver_now"


def test_planner_defer_keeps_activity_candidate_out_of_store_turn_contract() -> None:
    state = {
        "content": "我在广州这边",
        "normalized_content": "我在广州这边",
        "sop_delivery_manifest": build_sop_delivery_manifest(_activity_gate()),
        "conversation_history": ["用户: 我在广州这边"],
        "history_events": [],
    }
    payload = {
        "decision": "need_tools",
        "stage": "S1",
        "sales_progression": {
            "status": "continue",
            "target_stage": "store",
            "action": "confirm_store",
            "required_message_types": ["text"],
        },
        "sop_delivery_decision": {
            "action": "defer",
            "sop_pack_id": "s10_activity_intro",
            "reason": "本轮先完成门店区域确认",
        },
        "reply_contract": {"required_deliveries": [{"message_type": "text"}]},
        "tool_calls": [
            {
                "name": "customer_store_lookup",
                "query": "广州",
                "purpose": "existence",
                "location_specificity": "confirmed_region",
            }
        ],
        "reply_messages": [],
    }

    plan = build_planner_plan_v2(state, payload)

    assert plan["sop_delivery_decision"]["action"] == "defer"
    assert plan["authorized_sop_delivery_manifest"]["active"] is False
    assert plan["reply_contract"]["delivery_manifest_active"] is False
    assert [item["message_type"] for item in plan["reply_contract"]["required_deliveries"]] == ["text"]


def test_planner_deliver_now_activates_complete_activity_contract() -> None:
    state = {
        "content": "先问问价格",
        "normalized_content": "先问问价格",
        "sop_delivery_manifest": build_sop_delivery_manifest(_activity_gate()),
        "conversation_history": ["用户: 先问问价格"],
        "history_events": [],
    }
    payload = {
        "decision": "direct_reply",
        "stage": "S2",
        "sales_progression": {
            "status": "continue",
            "target_stage": "activity",
            "action": "deliver_value",
            "required_message_types": ["text", "image"],
            "source_pack_ids": ["s10_activity_intro"],
        },
        "sop_delivery_decision": {
            "action": "deliver_now",
            "sop_pack_id": "s10_activity_intro",
            "reason": "本轮直接完整回答活动价格",
        },
        "payment_decision": {"action": "none"},
        "reply_contract": {"required_deliveries": []},
        "tool_calls": [],
        "reply_messages": [{"type": "text", "content": "活动内容按完整话术包发送。"}],
    }

    plan = build_planner_plan_v2(state, payload)

    assert plan["sop_delivery_decision"]["action"] == "deliver_now"
    assert plan["authorized_sop_delivery_manifest"]["active"] is True
    assert [item["message_type"] for item in plan["reply_contract"]["required_deliveries"]] == [
        "text",
        "image",
        "text",
    ]


@pytest.mark.parametrize("scene_id", ["effect_definition_trust", "one_session_effect"])
def test_effect_scene_overrides_activity_and_payment_with_evidence_contract(scene_id: str) -> None:
    plan = build_planner_plan_v2(_effect_state(scene_id), _effect_model_payload(scene_id))

    assert plan["planner_decision"] == "need_tools"
    assert plan["main_blocker"] == "effect"
    assert plan["sales_progression"]["target_stage"] == "effect_proof"
    assert plan["payment_decision"]["action"] == "none"
    assert plan["authorized_sop_delivery_manifest"]["active"] is False
    assert [item["message_type"] for item in plan["reply_contract"]["required_deliveries"]] == [
        "text",
        "text",
        "image",
    ]
    case_tool = next(item for item in plan["required_tools"] if item.get("name") == "kb_search")
    assert case_tool["kb_name"] == "case_studies"

    validate_reply_consistency(_valid_effect_messages(), _effect_reply_state(plan))


@pytest.mark.parametrize(
    ("messages", "error"),
    [
        (
            [
                {
                    "type": "text",
                    "content": "亲，我们是做斑点改善的，绝大多数顾客一次就有很好的改善效果，活动价268元。",
                },
                {
                    "type": "text",
                    "content": "完成线上活动登记后可以到店免费做皮肤检测，门店结合您的具体情况讲解。",
                },
                {"type": "image", "content": {"url": CASE_IMAGE}},
            ],
            "effect_trust_price_or_deposit_not_allowed",
        ),
        (
            [
                {"type": "text", "content": "可能需要多次，具体看斑点深浅和时间。"},
                {
                    "type": "text",
                    "content": "完成线上活动登记后可以到店免费做皮肤检测，门店结合您的具体情况讲解。",
                },
                {"type": "image", "content": {"url": CASE_IMAGE}},
            ],
            "effect_trust_positive_answer_missing|effect_trust_conflicting_wording",
        ),
        (
            [
                {
                    "type": "text",
                    "content": "亲，我们是做斑点改善的，绝大多数顾客一次就有很好的改善效果。",
                },
                {
                    "type": "text",
                    "content": "完成线上活动登记后可以到店免费做皮肤检测，门店结合您的具体情况讲解。",
                },
                {"type": "image", "content": {"url": CASE_IMAGE}},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
            "effect_trust_delivery_sequence_mismatch|effect_trust_payment_collection_not_allowed",
        ),
    ],
)
def test_effect_contract_blocks_price_conflicts_and_payment(messages: list[dict], error: str) -> None:
    plan = build_planner_plan_v2(_effect_state(), _effect_model_payload())
    with pytest.raises(ValueError, match=error):
        validate_reply_consistency(messages, _effect_reply_state(plan))


def test_effect_contract_uses_authoritative_history_event_when_no_new_case_is_available() -> None:
    plan = build_planner_plan_v2(_effect_state(), _effect_model_payload())
    state = {
        **plan,
        "history_events": [
            {
                "event_id": "recent-case",
                "event_type": "case_image_sent",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "facts": {"image_urls": ["https://oss.example.test/recent-case.png"]},
            }
        ],
        "fact_envelope": {"structured_facts": {"case_facts": [{"status": "no_new_case_image"}]}},
    }
    messages = _valid_effect_messages()[:2]
    messages[0]["content"] = (
        "亲，您刚才看到的这张就是同类真实改善参考。我们是做斑点改善的，"
        "绝大多数顾客一次就有很好的改善效果。"
    )

    validate_reply_consistency(messages, state)


def test_similarity_warning_applies_even_when_reply_contains_image() -> None:
    warnings = collect_reply_soft_warnings(
        [
            {"type": "text", "content": "亲，现在活动是268元，先交10元预约金就可以登记。"},
            {"type": "image", "content": {"url": ACTIVITY_IMAGE}},
        ],
        {
            "conversation_history": [
                "小贝: 亲，现在活动是268元，先交10元预约金就可以登记。",
                "用户: 好的",
            ]
        },
    )

    assert any(item.get("detail") == "reply_too_similar_to_previous_assistant_message" for item in warnings)


def test_newer_platform_customer_message_supersedes_completed_reply() -> None:
    messages = [
        {
            "msgid": "trigger-1",
            "direction": "customer",
            "content": "不是去斑吗？",
            "created_at": "2026-08-09T23:35:00+08:00",
        },
        {
            "msgid": "assistant-1",
            "direction": "staff",
            "content": "正在生成",
            "created_at": "2026-08-09T23:35:10+08:00",
        },
        {
            "msgid": "customer-2",
            "direction": "customer",
            "content": "我问的是效果",
            "created_at": "2026-08-09T23:35:20+08:00",
        },
    ]

    result = newer_customer_message_after_trigger(
        messages,
        trigger_message_id="trigger-1",
        trigger_events=[{"msgid": "trigger-1", "created_at": datetime.now(timezone.utc).isoformat()}],
    )

    assert result["status"] == "checked"
    assert result["newer_customer_message"] is True
    assert result["newer_message_refs"] == ["customer-2"]


def test_missing_trigger_and_timestamp_reports_freshness_unavailable() -> None:
    result = newer_customer_message_after_trigger(
        [{"msgid": "other", "direction": "staff", "content": "历史回复"}],
        trigger_message_id="missing",
        trigger_events=[],
    )

    assert result == {
        "status": "unavailable",
        "newer_customer_message": False,
        "newer_message_refs": [],
        "reason": "trigger_message_not_found_and_timestamp_unavailable",
    }


def test_missing_platform_message_id_does_not_treat_trigger_echo_as_new_customer_turn() -> None:
    result = newer_customer_message_after_trigger(
        [
            {
                "direction": "customer",
                "content": "效果怎么样，有图吗",
                "msgtype": "text",
                "created_at": "2026-08-09T17:44:48.999+00:00",
            }
        ],
        trigger_message_id="trigger-1",
        trigger_events=[
            {
                "msgid": "trigger-1",
                "msgtime": "1786297488798",
                "msgtype": "text",
                "content": "效果怎么样，有图吗",
            }
        ],
    )

    assert result["status"] == "checked"
    assert result["newer_customer_message"] is False


def test_missing_platform_message_id_still_detects_different_new_customer_turn() -> None:
    result = newer_customer_message_after_trigger(
        [
            {
                "direction": "customer",
                "content": "效果怎么样，有图吗",
                "msgtype": "text",
                "created_at": "2026-08-09T17:44:48.999+00:00",
            },
            {
                "direction": "customer",
                "content": "我问的是能不能一次改善",
                "msgtype": "text",
                "created_at": "2026-08-09T17:44:49.200+00:00",
            },
        ],
        trigger_message_id="trigger-1",
        trigger_events=[
            {
                "msgid": "trigger-1",
                "msgtime": "1786297488798",
                "msgtype": "text",
                "content": "效果怎么样，有图吗",
            }
        ],
    )

    assert result["newer_customer_message"] is True
    assert result["newer_message_refs"] == ["conv_002"]


class _FreshnessCoordinator:
    async def is_latest(self, record: PlatformReplyRecord) -> bool:
        return True

    def control_for_superseded(self, record: PlatformReplyRecord) -> dict:
        return {"mode": "superseded", "message_id": record.message_id}


class _FreshnessConversationClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def fetch_conversation(self, **kwargs: object) -> dict:
        return self.result


class _UnusedGraph:
    async def ainvoke(self, state: dict) -> dict:
        return state


class _UnusedTraceLogger:
    def write_run(self, state: dict) -> str:
        return "unused.json"


class _UnusedRepository:
    pass


def _freshness_record() -> PlatformReplyRecord:
    return PlatformReplyRecord(
        request_id="run-1",
        customer_key="corp:DY258:customer",
        generation_id="generation-1",
        original_content="不是去斑吗？",
        merged_customer_messages=["不是去斑吗？"],
        image_urls=[],
        merged_input_events=[],
        started_at=datetime.now(timezone.utc),
        message_id="trigger-1",
    )


def _freshness_request() -> ChatRequest:
    return ChatRequest(
        content="不是去斑吗？",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        wechat="DY258",
        request_context={"msgid": "trigger-1"},
    )


def test_runtime_freshness_guard_clears_text_image_and_card_after_new_customer_turn() -> None:
    runtime = ChatRuntime(
        full_graph=_UnusedGraph(),
        trace_logger=_UnusedTraceLogger(),
        repository=_UnusedRepository(),
        platform_reply_coordinator=_FreshnessCoordinator(),
        outreach_send_client=_FreshnessConversationClient(
            {
                "status": "ok",
                "messages": [
                    {"msgid": "trigger-1", "direction": "customer", "content": "不是去斑吗？"},
                    {"msgid": "customer-2", "direction": "customer", "content": "我问的是效果"},
                ],
            }
        ),
    )
    state = {
        "corp_id": "corp",
        "wechat": "DY258",
        "customer_id": "customer",
        "external_userid": "external",
        "request_context": {"msgid": "trigger-1"},
        "reply_messages": [
            {"type": "text", "content": "旧回复"},
            {"type": "image", "content": {"url": CASE_IMAGE}},
            {"type": "payment_collection", "content": {"amount": 10}},
        ],
        "trace": [],
        "errors": [],
        "warnings": [],
    }

    result = asyncio.run(
        runtime._apply_platform_freshness_guard(
            request=_freshness_request(),
            state=state,
            control_record=_freshness_record(),
        )
    )

    assert result["reply_messages"] == []
    assert result["reply_source"] == "platform_superseded"
    assert result["reply_freshness_check"]["status"] == "superseded"
    assert result["reply_freshness_check"]["newer_message_refs"] == ["customer-2"]


def test_runtime_freshness_guard_keeps_latest_reply_when_refresh_is_unavailable() -> None:
    runtime = ChatRuntime(
        full_graph=_UnusedGraph(),
        trace_logger=_UnusedTraceLogger(),
        repository=_UnusedRepository(),
        platform_reply_coordinator=_FreshnessCoordinator(),
        outreach_send_client=_FreshnessConversationClient({"status": "failed", "error": "timeout"}),
    )
    state = {
        "corp_id": "corp",
        "wechat": "DY258",
        "customer_id": "customer",
        "external_userid": "external",
        "request_context": {"msgid": "trigger-1"},
        "reply_messages": [{"type": "text", "content": "当前回复"}],
        "trace": [],
        "errors": [],
        "warnings": [],
    }

    result = asyncio.run(
        runtime._apply_platform_freshness_guard(
            request=_freshness_request(),
            state=state,
            control_record=_freshness_record(),
        )
    )

    assert result["reply_messages"] == [{"type": "text", "content": "当前回复"}]
    assert result["reply_freshness_check"]["status"] == "unavailable"
    assert any(item.get("message") == "freshness_check_unavailable" for item in result["warnings"])
