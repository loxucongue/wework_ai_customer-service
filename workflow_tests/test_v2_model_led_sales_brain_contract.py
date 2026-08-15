from __future__ import annotations

import inspect

from app.graph.nodes.parallel_reply_chain import (
    _content_gate_shared_context,
    _tool_planner_shared_context,
    _v2_activity_offer_delivered,
)
from app.graph.nodes.v2_derived_observations import build_v2_derived_observations
from app.graph.nodes.reply_quality import collect_reply_observation_metrics
from app.graph.nodes.v2_reply_admission import validate_v2_reply_admission
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT
from app.prompts.sop_chat_gate import (
    PARALLEL_CONTENT_GATE_SYSTEM_PROMPT,
    SOP_CHAT_GATE_SYSTEM_PROMPT,
    build_sop_chat_gate_messages,
)


def test_parallel_reply_receives_model_led_sales_principles_without_scene_catalog() -> None:
    rules = parallel_reply_business_rules_for_model()

    assert rules["SALES PRINCIPLES"]["principles"]
    assert rules["SALES PRINCIPLES"]["anti_patterns"]
    assert "scene_catalog" not in rules
    assert "conversion_stage" not in str(rules)


def test_derived_observations_are_raw_rebuildable_values_without_predicates() -> None:
    result = build_v2_derived_observations(
        conversation=[
            {
                "message_ref": "chat_1",
                "role": "assistant",
                "content": "活动图已发",
                "sent_at": "2026-08-13T10:00:00+08:00",
            },
            {
                "message_ref": "chat_2",
                "role": "customer",
                "content": "我考虑一下",
                "sent_at": "2026-08-13T10:01:00+08:00",
            },
        ],
        history_events=[
            {
                "event_type": "activity_intro_image_sent",
                "created_at": "2026-08-13T10:00:00+08:00",
                "source_ref": "event:activity_1",
            },
            {
                "event_type": "v2_reply_model_observation",
                "created_at": "2026-08-13T10:00:30+08:00",
                "source_ref": "event:observation_1",
                "facts": {
                    "primary_objective": "说明活动价值",
                    "customer_friction_observation": "客户说要考虑",
                },
            },
        ],
        current_message={
            "content": "我考虑一下",
            "sent_at": "2026-08-13T10:01:00+08:00",
        },
    )

    serialized = str(result).lower()
    assert result["recent_asset_deliveries"][0]["source_refs"]
    assert result["prior_model_observations"][0]["authority"] == "v2_prior_model_observation_not_customer_fact"
    assert "high_intent" not in serialized
    assert "should_close" not in serialized
    assert "objection_resolved" not in serialized


def test_derived_observations_keep_v2_and_v3_model_observations_separate() -> None:
    history_events = [
        {
            "event_type": "v2_reply_model_observation",
            "event_id": "v2_obs",
            "created_at": "2026-08-13T10:00:00+08:00",
            "facts": {
                "primary_objective": "V2 目标",
                "customer_friction_observation": "V2 阻力",
            },
        },
        {
            "event_type": "v3_reply_model_observation",
            "event_id": "v3_obs",
            "created_at": "2026-08-13T10:01:00+08:00",
            "facts": {
                "primary_objective": "V3 目标",
                "customer_friction_observation": "V3 阻力",
            },
        },
    ]

    v2_result = build_v2_derived_observations(
        conversation=[],
        history_events=history_events,
        current_message={"content": "继续", "sent_at": "2026-08-13T10:02:00+08:00"},
        interface_version="v2",
    )
    v3_result = build_v2_derived_observations(
        conversation=[],
        history_events=history_events,
        current_message={"content": "继续", "sent_at": "2026-08-13T10:02:00+08:00"},
        interface_version="v3",
    )

    assert v2_result["prior_model_observations"][0]["primary_objective"] == "V2 目标"
    assert v3_result["prior_model_observations"][0]["primary_objective"] == "V3 目标"
    assert v3_result["prior_model_observations"][0]["authority"] == "v3_prior_model_observation_not_customer_fact"


def test_tool_planner_cannot_receive_sales_observations() -> None:
    payload = _tool_planner_shared_context(
        {
            "shared_context": {
                "derived_observations": {"prior_model_observations": [{"primary_objective": "close"}]},
                "content_indexes": {"available_sop": {}},
                "sales_guidance": {"anything": True},
                "authoritative_facts": {},
            }
        }
    )

    assert "derived_observations" not in payload
    assert "content_indexes" not in payload
    assert "sales_guidance" not in payload


def test_content_gate_receives_delivery_observations_but_not_prior_model_judgment() -> None:
    payload = _content_gate_shared_context(
        {
            "shared_context": {
                "derived_observations": {
                    "recent_asset_deliveries": [{"asset_kind": "activity_image"}],
                    "prior_model_observations": [{"primary_objective": "close"}],
                }
            }
        }
    )

    observations = payload["derived_observations"]
    assert observations["recent_asset_deliveries"]
    assert "prior_model_observations" not in observations


def test_parallel_content_gate_uses_the_retrieval_prompt_not_the_legacy_scene_prompt() -> None:
    messages = build_sop_chat_gate_messages(
        {
            "reply_chain_mode": "parallel_candidate_only",
            "content_assets": [],
            "candidate_limit": 2,
        }
    )

    assert messages[0]["content"] == PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert messages[0]["content"] != SOP_CHAT_GATE_SYSTEM_PROMPT
    assert "selected_scene_id" not in messages[0]["content"]
    assert "不回复客户" in messages[0]["content"]


def test_v2_admission_has_no_visible_text_or_semantic_regex_logic() -> None:
    source = inspect.getsource(validate_v2_reply_admission)

    assert "re.search" not in source
    assert "re.find" not in source
    assert "message_text" not in source
    assert "visible_text=False" in source


def test_v2_admission_rejects_adopted_asset_when_structured_media_is_missing() -> None:
    state = {
        "evidence_join": {
            "schema_version": "parallel_reply_input_v2",
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "text", "content": "活动介绍"},
                        {"type": "image", "content": {"url": "https://example.test/activity.png"}},
                    ],
                }
            ],
        },
        "reply_selected_content_ids": [],
        "reply_used_fact_refs": ["content_asset:s10_activity_intro"],
    }

    try:
        validate_v2_reply_admission(
            [{"type": "text", "content": "活动介绍"}],
            state,
        )
    except ValueError as exc:
        assert "selected_content_delivery_missing:content_id=s10_activity_intro" in str(exc)
    else:
        raise AssertionError("adopted content asset must deliver its structured media")


def test_v2_payment_material_requires_structured_activity_delivery() -> None:
    assert not _v2_activity_offer_delivered(
        sop_progress={},
        sent_messages={},
        history_events=[
            {
                "event_type": "conversation_message",
                "content": "268元，先付10元，到店抵扣",
            }
        ],
    )
    assert _v2_activity_offer_delivered(
        sop_progress={"completed_pack_ids": ["s10_activity_intro"]},
        sent_messages={},
        history_events=[],
    )


def test_prompts_preserve_node_power_boundaries_without_scene_matching() -> None:
    assert "只有你负责理解客户、选择销售目标" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不是场景匹配器" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不输出客户话术、销售动作、工具调用或写操作" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    for forbidden in ("selected_scene_id", "conversion_stage", "customer_type"):
        assert forbidden not in PARALLEL_REPLY_SYSTEM_PROMPT
        assert forbidden not in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_reply_prompt_requires_real_progress_and_complete_deposit_facts() -> None:
    assert "推进是本轮实际完成交付" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "`sales_judgment.posture` 不得停在 `answer`" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "每位先付10元锁活动资格、到店抵扣、做再付258元、未做或不满意可退" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_authoritative_facts_rank_above_historical_assistant_text() -> None:
    authority_line = next(
        line for line in PARALLEL_REPLY_SYSTEM_PROMPT.splitlines() if line.startswith("冲突时依次相信")
    )
    assert authority_line.index("`rules.AUTHORITATIVE FACTS`") < authority_line.index("完整聊天")
    assert "历史聊天只证明" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_v2_repeat_similarity_is_measurement_only() -> None:
    metrics = collect_reply_observation_metrics(
        [{"type": "text", "content": "活动价是268元，我给您说清楚。"}],
        {
            "conversation_history": ["小贝: 活动价是268元，我给您说清楚。"],
            "evidence_join": {"schema_version": "parallel_reply_input_v2"},
        },
    )
    assert metrics["previous_assistant_text_similarity"] == 1.0
    assert metrics["measurement_only"] is True
