from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import pytest

from app.schemas import ChatRequest
from app.graph.nodes import parallel_reply_chain
from app.graph.nodes.parallel_reply_chain import (
    _authoritative_order_payment_facts,
    _authoritative_registration_facts,
    _commit_action_violations,
    _conversation,
    _gate_conversation_history,
    _merge_tool_calls,
    _normalize_read_only_tool_calls,
    _protocol_required_read_only_tools,
    _run_tool_planner,
    create_evidence_join_node,
    create_parallel_evidence_node,
    create_shared_context_node,
    parallel_reply_payload,
)
from app.graph.graph_builder import ReplyGraphs
from app.graph.planner.runtime_plan import planner_public_route
from app.graph.nodes.reply_nodes import (
    _canonical_assessment_refs,
    _case_image_urls,
    _maybe_build_required_payment_collection_fallback,
    _needs_strong_reply_model,
    _prepare_structural_messages,
    _parallel_reply_repair_context,
    _reply_recovery_messages,
    _reply_repair_hint,
    _reply_retry_messages,
    _reply_structural_repair_guard,
    _reply_validation_state,
    _validate_parallel_raw_reply_schema,
    create_synthesize_reply_node,
)
from app.prompts.sop_chat_gate import PARALLEL_CONTENT_GATE_SYSTEM_PROMPT, SOP_CHAT_GATE_SYSTEM_PROMPT
from app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.graph.nodes.reply_validation import debug_message_contents, validate_reply_consistency, validated_model_messages
from app.services.payment_collection import payment_collection_context
from app.services.sop_execution_service import (
    SopExecutionService,
    _chat_selector_input,
    _parallel_content_gate_output_violations,
    _parallel_content_candidate,
    _parallel_sop_asset_summary,
)


class _TraceLogger:
    @contextmanager
    def node(self, state, name, input_snapshot):
        entry = {"node": name, "tool_calls": []}
        state.setdefault("trace", []).append(entry)
        yield {"entry": entry}


def _parallel_state(content: str = "怎么付") -> dict:
    conversation = [
        {
            "message_ref": "current_message",
            "role": "customer",
            "content": content,
        }
    ]
    return {
        "content": content,
        "normalized_content": content,
        "shared_context": {"conversation": conversation},
        "evidence_join": {"schema_version": "reply_chain_evidence_join_v1"},
    }


def test_authoritative_registration_facts_do_not_treat_platform_display_name_as_registration() -> None:
    state = {
        "customer_context": {
            "customer": {
                "name": "企微展示昵称",
            }
        },
        "customer_basic_info": {},
    }

    assert _authoritative_registration_facts(state) == {}


def test_parallel_gate_only_nominates_content_assets() -> None:
    prompt = PARALLEL_CONTENT_GATE_SYSTEM_PROMPT

    assert "0-2" in prompt
    assert "candidate_assets" in prompt
    assert "不回复客户" in prompt
    assert "不决定销售动作" in prompt
    assert "selected_scene_id" not in prompt
    assert "reference_messages" not in prompt


def test_parallel_gate_asset_exposes_plain_text_without_legacy_routing_metadata() -> None:
    pack = {
        "id": "s10_activity_intro",
        "name": "activity offer",
        "purpose": "explain the offer",
        "asset_role": "activity_offer",
        "mainline_stage": "activity_and_price",
        "order": 150,
        "triggers": ["legacy trigger"],
        "reply_messages": [
            {"type": "text", "order": 1, "content": {"text": "268 offer facts"}},
            {"type": "image", "order": 2, "content": {"url": "https://example.test/offer.png"}},
        ],
    }

    summary = _parallel_sop_asset_summary(pack)
    candidate = _parallel_content_candidate(
        pack,
        {
            "relevance": "supporting",
            "render_strategy": "adaptable",
            "evidence_refs": ["current_message"],
        },
    )

    assert summary["approved_points"] == ["268 offer facts"]
    assert candidate["approved_points"] == ["268 offer facts"]
    for legacy_key in ("mainline_stage", "order", "triggers", "prerequisites"):
        assert legacy_key not in summary


def test_parallel_gate_selector_uses_shared_facts_without_legacy_scene_catalogs() -> None:
    request = ChatRequest(content="current", customer_id="sim_customer", corp_id="sim_corp")
    payload = _chat_selector_input(
        request,
        [
            {
                "id": "s10_activity_intro",
                "name": "activity offer",
                "purpose": "explain the offer",
                "asset_role": "activity_offer",
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "268 offer facts"}},
                ],
            }
        ],
        shared_context={
            "current_time": {"iso": "2026-08-10T10:00:00+08:00"},
            "current_message": {"content": "current"},
            "conversation": [
                {"message_ref": "current_message", "role": "customer", "content": "current"},
            ],
            "authoritative_facts": {
                "sop_progress": {"completed_pack_ids": ["s10_new_customer_opening"]},
            },
        },
    )

    assert payload["unfinished_sops"][0]["approved_points"] == ["268 offer facts"]
    assert payload["authoritative_context"]["content_delivery_progress"] == {
        "completed_pack_ids": ["s10_new_customer_opening"]
    }
    assert "mainline_progress" not in payload
    assert "mainline" not in payload
    assert "precision_qa_index" not in payload


def test_parallel_gate_validator_accepts_content_id_catalog_shape() -> None:
    selector_input = {
        "content_assets": [{"content_id": "s10_activity_intro"}],
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "price"},
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": "s10_activity_intro",
                "relevance": "direct",
                "evidence_purpose": "explain offer value",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            }
        ]
    }

    assert _parallel_content_gate_output_violations(selector_output, selector_input) == []


def test_parallel_gate_rejects_deposit_asset_without_prior_activity_evidence() -> None:
    selector_input = {
        "content_assets": [
            {
                "content_id": "s10_activity_intro",
                "asset_role": "activity_offer",
                "delivery_status": "available",
            },
            {
                "content_id": "s10_deposit_close",
                "asset_role": "deposit_close",
                "delivery_status": "available",
                "requires_prior_asset_roles": ["activity_offer"],
            },
        ],
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "怎么报名"},
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": "s10_activity_intro",
                "relevance": "direct",
                "evidence_purpose": "explain the activity",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            },
            {
                "content_id": "s10_deposit_close",
                "relevance": "supporting",
                "evidence_purpose": "explain payment",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            },
        ]
    }

    assert "candidate_asset_missing_required_role:s10_deposit_close:activity_offer" in (
        _parallel_content_gate_output_violations(selector_output, selector_input)
    )


def test_parallel_gate_accepts_deposit_asset_after_structured_activity_delivery() -> None:
    selector_input = {
        "content_assets": [
            {
                "content_id": "s10_activity_intro",
                "asset_role": "activity_offer",
                "delivery_status": "completed",
            },
            {
                "content_id": "s10_deposit_close",
                "asset_role": "deposit_close",
                "delivery_status": "available",
                "requires_prior_asset_roles": ["activity_offer"],
            },
        ],
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "怎么付"},
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": "s10_deposit_close",
                "relevance": "direct",
                "evidence_purpose": "provide deposit facts",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            }
        ]
    }

    assert _parallel_content_gate_output_violations(selector_output, selector_input) == []


def test_parallel_gate_rejects_prior_assistant_evidence_as_asset_completion() -> None:
    selector_input = {
        "content_assets": [
            {
                "content_id": "s10_deposit_close",
                "asset_role": "deposit_close",
                "delivery_status": "available",
                "requires_prior_asset_roles": ["activity_offer"],
            },
        ],
        "conversation_evidence": [
            {"message_ref": "conv_006", "direction": "assistant", "content": "prior offer"},
            {"message_ref": "current_message", "direction": "customer", "content": "怎么付"},
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": "s10_deposit_close",
                "relevance": "direct",
                "evidence_purpose": "provide deposit facts",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
                "prerequisite_evidence_refs": ["conv_006"],
            }
        ]
    }

    violations = _parallel_content_gate_output_violations(selector_output, selector_input)

    assert "candidate_asset_forbidden_prerequisite_refs:s10_deposit_close" in violations
    assert "candidate_asset_missing_required_role:s10_deposit_close:activity_offer" in violations


def test_parallel_shared_content_catalog_does_not_expose_mainline_routing() -> None:
    class _PackService:
        def load(self):
            return {
                "packs": [
                    {
                        "id": "s10_activity_intro",
                        "enabled": True,
                        "scope": "chat_gate",
                        "name": "activity offer",
                        "purpose": "explain offer value",
                        "asset_role": "activity_offer",
                        "sop_category": "activity_intro",
                        "mainline_stage": "activity_and_price",
                        "order": 150,
                        "send_once": True,
                        "reply_messages": [
                            {"type": "text", "order": 1, "content": {"text": "268 offer facts"}},
                        ],
                    }
                ]
            }

    service = object.__new__(SopExecutionService)
    service.sop_reply_pack_service = _PackService()

    catalog = service.reply_chain_content_catalog()
    asset = catalog["sop_packs"][0]

    assert asset["asset_role"] == "activity_offer"
    assert "mainline_stage" not in asset
    assert "order" not in asset
    assert "send_once" not in asset


def test_parallel_tool_planner_independently_queries_missing_case_images() -> None:
    assert "Gate 与你并行执行" in parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT
    assert "必须独立规划 `kb_search(kb_name=case_studies)`" in parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT
    assert "最终 Reply 会对 Gate 候选与工具事实去重" in parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT


def test_parallel_content_gate_does_not_use_opening_asset_for_substantive_questions() -> None:
    assert "`opening_context` 只提供初始开场素材" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "不要按词语命中或配置顺序补流程" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "不能替代客户当前实质问题所需的证据" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_parallel_reply_prompt_uses_history_without_fixed_short_ack_script() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "完整带时间对话" in prompt
    assert "不要继续追问" in prompt
    assert "每轮只选择一个主要目标" in prompt
    assert "可以只回答或暂停" in prompt
    assert "好/好的/嗯/可以" not in prompt


def test_parallel_reply_rule_view_keeps_unique_sections_without_repeating_layered_facts() -> None:
    rules = parallel_reply_business_rules_for_model()

    assert rules["AUTHORITATIVE FACTS"]["offer"]["new_customer_price"] == 268
    assert "不能靠口头免费保留" in rules["AUTHORITATIVE FACTS"]["offer"]["reservation_completion_rule"]
    assert rules["AUTHORITATIVE FACTS"]["transaction_policy"]["appointment_flow_mode"] == "registration_only"
    payment_blocks = rules["AUTHORITATIVE FACTS"]["transaction_policy"]["payment_hard_blocks"]
    assert "manual_transfer_method" in payment_blocks
    assert "unverified_oral_paid_claim" in payment_blocks
    assert rules["AUTHORITATIVE FACTS"]["customer_visible_evidence_policy"]["effect_confidence"]
    assert "面对面皮肤检测和评估" in rules["AUTHORITATIVE FACTS"]["health_risk_policy"]["in_store_assessment"]
    assert rules["TOOL FACT BOUNDARIES"]["customer_store_lookup"]
    assert rules["CONTENT ASSET POLICY"]["gate_candidates_are_optional_evidence"] is True
    principles = rules["SALES PRINCIPLES"]
    assert principles["source"] == "shared_context.sales_guidance.principles"
    assert "no raw source replies" in principles["runtime_contract"]
    assert rules["CONTENT ASSET POLICY"]["precision_examples_are_offline_only"] is True
    assert "offer_facts" not in rules
    assert "transaction_policy" not in rules
    assert "conversion_psychology" not in rules


def test_parallel_reply_treats_immediately_prior_complete_offer_as_prior_evidence() -> None:
    assert "当前消息之前已经真实介绍过本次活动与价格" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "已经说清" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_assessments_use_real_customer_message_refs_for_amount() -> None:
    state = _parallel_state("我和朋友两个人")
    payload = {
        "action": "payment",
        "safety_assessment": {"status": "none", "evidence_refs": []},
        "party_size_assessment": {
            "status": "known",
            "party_size": 2,
            "evidence_refs": ["current_message"],
        },
    }

    validation_state = _reply_validation_state(state, payload)

    assert payment_collection_context(state=validation_state, messages=[]) == {
        "participants": 2,
        "amount": 20,
        "over_limit": False,
    }


def test_reply_assessment_rejects_hallucinated_message_ref() -> None:
    state = _parallel_state("我和朋友两个人")
    payload = {
        "action": "payment",
        "safety_assessment": {"status": "none", "evidence_refs": []},
        "party_size_assessment": {
            "status": "known",
            "party_size": 2,
            "evidence_refs": ["missing_history_99"],
        },
    }

    with pytest.raises(ValueError, match="party_size_assessment_has_invalid_evidence_ref"):
        _reply_validation_state(state, payload)


def test_parallel_reply_payload_lists_exact_customer_message_refs() -> None:
    state = {
        "evidence_join": {
            "shared_context": {
                "conversation": [
                    {"message_ref": "conv_001", "role": "assistant", "content": "您好"},
                    {"message_ref": "conv_002", "role": "customer", "content": "先不用了"},
                ]
            }
        }
    }

    payload = parallel_reply_payload(state)

    assert payload["valid_customer_message_refs"] == ["current_message", "conv_002"]


def test_reply_schema_normalizes_nonempty_string_items_as_text_messages() -> None:
    messages = validated_model_messages(
        {"reply_messages": ["现在活动价是268元。", "", {"type": "text", "content": "包含淡斑和皮肤检测。"}]},
        {},
    )

    assert messages == [
        {"type": "text", "order": 1, "content": "现在活动价是268元。"},
        {"type": "text", "order": 2, "content": "包含淡斑和皮肤检测。"},
    ]


def test_parallel_reply_payload_only_exposes_completed_activity_offer_as_deposit_evidence() -> None:
    state = {
        "evidence_join": {
            "shared_context": {
                "conversation": [],
                "authoritative_facts": {
                    "sop_progress": {
                        "completed_pack_ids": ["s10_need_and_case", "s10_activity_intro"]
                    }
                },
                "content_indexes": {
                    "available_sop": {
                        "sop_packs": [
                            {
                                "content_id": "s10_need_and_case",
                                "asset_role": "effect_evidence",
                            },
                            {
                                "content_id": "s10_activity_intro",
                                "asset_role": "activity_offer",
                            },
                        ]
                    }
                },
            }
        }
    }

    payload = parallel_reply_payload(state)

    assert "sop_completed:s10_activity_intro" in payload["valid_deposit_evidence_refs"]
    assert "sop_completed:s10_need_and_case" in payload["valid_deposit_evidence_refs"]
    assert payload["structured_prior_activity_refs"] == ["sop_completed:s10_activity_intro"]
    assert payload["prior_assistant_message_refs"] == []
    assert payload["prior_message_and_delivery_refs"] == [
        "current_message",
        "sop_completed:s10_need_and_case",
        "sop_completed:s10_activity_intro",
    ]
    assert payload["structured_delivered_assets"] == [
        {
            "ref": "sop_completed:s10_need_and_case",
            "content_id": "s10_need_and_case",
            "asset_role": "effect_evidence",
        },
        {
            "ref": "sop_completed:s10_activity_intro",
            "content_id": "s10_activity_intro",
            "asset_role": "activity_offer",
        },
    ]
    assert "sop_completed:s10_need_and_case" in payload["prior_message_and_delivery_refs"]


def test_parallel_reply_payload_exposes_only_gate_nominated_content_ids() -> None:
    state = {
        "evidence_join": {
            "shared_context": {"conversation": []},
            "content_candidates": [
                {"content_id": "s10_activity_intro"},
                {"content_id": "s10_deposit_close"},
            ],
        }
    }

    payload = parallel_reply_payload(state)

    assert payload["allowed_selected_content_ids"] == ["s10_activity_intro", "s10_deposit_close"]
    assert payload["content_candidate_reference_options"] == [
        {
            "content_id": "s10_activity_intro",
            "used_fact_ref": "content_asset:s10_activity_intro",
        },
        {
            "content_id": "s10_deposit_close",
            "used_fact_ref": "content_asset:s10_deposit_close",
        },
    ]


def test_parallel_reply_payload_exposes_prior_assistant_refs_without_gate_semantic_label() -> None:
    state = {
        "evidence_join": {
            "shared_context": {
                "conversation": [
                    {
                        "message_ref": "offer_001",
                        "role": "assistant",
                        "content": "活动总价268元，包含范围已经说明。",
                    }
                ],
                "authoritative_facts": {"sop_progress": {"completed_pack_ids": []}},
                "content_indexes": {"available_sop": {"sop_packs": []}},
            },
            "content_candidates": [],
        }
    }

    payload = parallel_reply_payload(state)

    assert payload["structured_prior_activity_refs"] == []
    assert payload["prior_assistant_message_refs"] == ["offer_001"]
    assert "deposit_reference_options" not in payload
    assert payload["evidence"]["shared_context"]["conversation"][0]["message_ref"] == "offer_001"


def test_parallel_reply_payload_removes_duplicate_raw_store_inventory_only_from_reply_view() -> None:
    state = {
        "evidence_join": {
            "shared_context": {
                "conversation": [],
                "authoritative_facts": {
                    "visible_store_scope": {"allowed_store_ids": ["10"]},
                    "raw_visible_store_records": [
                        {"store_id": "10", "large_unused_payload": "x" * 10000}
                    ],
                },
            },
            "content_candidates": [],
        }
    }

    payload = parallel_reply_payload(state)

    reply_facts = payload["evidence"]["shared_context"]["authoritative_facts"]
    assert "raw_visible_store_records" not in reply_facts
    assert reply_facts["visible_store_scope"] == {"allowed_store_ids": ["10"]}
    assert state["evidence_join"]["shared_context"]["authoritative_facts"][
        "raw_visible_store_records"
    ][0]["store_id"] == "10"


def test_parallel_reply_payload_surfaces_current_store_delivery_as_an_option() -> None:
    state = {
        "evidence_join": {
            "shared_context": {"conversation": [], "current_message": {"content": "发门店"}},
            "tool_facts": {
                "customer_store_lookup": {
                    "store_resolution_fact": {
                        "status": "send_single",
                        "delivery_store_ids": ["601"],
                        "candidate_search_complete": True,
                        "ranking_method": "haversine",
                    }
                }
            },
        }
    }

    payload = parallel_reply_payload(state)

    assert list(payload)[:7] == [
        "schema_version",
        "structured_delivery_options",
        "tool_fact_reference_options",
        "authoritative_fact_reference_options",
        "registration_fact_status",
        "store_fact_status",
        "evidence",
    ]
    assert payload["store_fact_status"] == {
        "status": "",
        "raw_place": "",
        "missing_facts": [],
        "resolved_admin": {"province": "", "city": "", "district": ""},
        "candidate_regions": [],
        "store_candidate_regions": [],
        "candidate_store_count": 0,
        "delivery_store_ids": [],
        "ranking_method": "",
        "source": "normalized_tool_facts",
    }

    assert payload["structured_delivery_options"] == {
        "store_address": {
            "fact_ref": "tool_fact:customer_store_lookup",
            "status": "send_single",
            "available_store_ids": ["601"],
            "message_payloads": [
                {"type": "store_address", "content": {"store_id": "601"}},
            ],
            "candidate_search_complete": True,
            "ranking_method": "haversine",
            "source": "current_turn_tool_fact",
        }
    }
    assert payload["tool_fact_reference_options"] == [
        {
            "ref": "tool_fact:customer_store_lookup",
            "tool_name": "customer_store_lookup",
        }
    ]
    validation_state = _reply_validation_state(
        state,
        {
            "reply_messages": [{"type": "store_address", "content": {"store_id": "601"}}],
            "used_fact_refs": ["current_message", "tool_fact:customer_store_lookup"],
            "selected_content_ids": [],
            "action": "none",
            "payment_assessment": {"status": "none", "evidence_refs": []},
            "deposit_evidence": {},
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
            "sales_judgment": {"posture": "answer"},
            "structured_delivery_decisions": [
                {
                    "fact_ref": "tool_fact:customer_store_lookup",
                    "decision": "deliver",
                    "reason": "客户正在索要门店位置",
                }
            ],
            "commit_actions": [],
        },
    )
    assert "tool_fact:customer_store_lookup" in validation_state["reply_used_fact_refs"]


def test_parallel_reply_payload_summarizes_paid_registration_field_presence() -> None:
    state = _parallel_state("付好了")
    shared_context = {
        "current_message": {"content": "付好了"},
        "conversation": [],
        "authoritative_facts": {
            "orders_and_payment": {
                "resolved_payment": {
                    "deposit_state": "paid_by_platform_transfer_event",
                }
            },
            "registration_facts": {
                "source": "structured_registration",
                "customer_name": "张三",
            },
        },
    }
    state["shared_context"] = shared_context
    state["evidence_join"] = {
        "schema_version": "reply_chain_evidence_join_v1",
        "shared_context": shared_context,
        "content_candidates": [],
        "tool_facts": {},
    }

    payload = parallel_reply_payload(state)

    assert payload["registration_fact_status"] == {
        "authoritative_paid": True,
        "collected_fields": ["customer_name"],
        "missing_fields": ["customer_mobile"],
        "source": "authoritative_facts.registration_facts",
    }


def test_parallel_reply_payload_surfaces_location_ambiguity_without_choosing_answer() -> None:
    state = _parallel_state("广州惠州")
    state["evidence_join"] = {
        "schema_version": "reply_chain_evidence_join_v1",
        "shared_context": {
            "current_message": {"content": "广州惠州"},
            "conversation": [],
            "authoritative_facts": {},
        },
        "content_candidates": [],
        "tool_facts": {},
        "normalized_tool_facts": {
            "missing_facts": ["confirmed_city_or_district"],
            "structured_facts": {
                "store_lookup_status": {"status": "need_location_confirmation"},
                "store_resolution_fact": {
                    "status": "need_location_confirmation",
                    "raw_place": "广州惠州",
                    "location_evidence": {
                        "province": "广东省",
                        "geocode_candidate_regions": [
                            {"province": "广东省", "city": "广州市", "district": "天河区"},
                            {"province": "广东省", "city": "惠州市", "district": "惠城区"},
                        ],
                    },
                    "ranking_method": "scope_match",
                },
            },
        },
    }

    status = parallel_reply_payload(state)["store_fact_status"]

    assert status["status"] == "need_location_confirmation"
    assert status["raw_place"] == "广州惠州"
    assert status["missing_facts"] == ["confirmed_city_or_district"]
    assert [item["city"] for item in status["candidate_regions"]] == ["广州市", "惠州市"]


def test_parallel_reply_payload_surfaces_store_candidate_regions_for_clarification() -> None:
    state = _parallel_state("广州这边最近是哪家")
    state["evidence_join"] = {
        "schema_version": "reply_chain_evidence_join_v1",
        "shared_context": {
            "current_message": {"content": "广州这边最近是哪家"},
            "conversation": [],
            "authoritative_facts": {},
        },
        "content_candidates": [],
        "tool_facts": {},
        "normalized_tool_facts": {
            "missing_facts": [],
            "structured_facts": {
                "store_lookup_status": {
                    "status": "need_location",
                    "province": "广东省",
                    "city": "广州市",
                },
                "store_resolution_fact": {
                    "status": "need_location",
                    "visible_candidate_count": 4,
                    "location_evidence": {"province": "广东省", "city": "广州市"},
                },
                "store_facts": [
                    {"province": "广东省", "city": "广州市", "district": "天河区"},
                    {"province": "广东省", "city": "广州市", "district": "番禺区"},
                    {"province": "广东省", "city": "广州市", "district": "天河区"},
                ],
            },
        },
    }

    status = parallel_reply_payload(state)["store_fact_status"]

    assert status["candidate_store_count"] == 4
    assert [item["district"] for item in status["store_candidate_regions"]] == ["天河区", "番禺区"]


def test_parallel_reply_payload_reads_store_delivery_from_normalized_tool_facts() -> None:
    state = {
        "evidence_join": {
            "shared_context": {"conversation": [], "current_message": {"content": "定位卡"}},
            "tool_facts": {"customer_store_lookup": {"status": "ok"}},
            "normalized_tool_facts": {
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "send_multiple",
                        "delivery_store_ids": ["601", "602"],
                        "candidate_search_complete": True,
                        "ranking_method": "haversine",
                    }
                }
            },
        }
    }

    payload = parallel_reply_payload(state)

    assert payload["structured_delivery_options"]["store_address"] == {
        "fact_ref": "tool_fact:customer_store_lookup",
        "status": "send_multiple",
        "available_store_ids": ["601", "602"],
        "message_payloads": [
            {"type": "store_address", "content": {"store_id": "601"}},
            {"type": "store_address", "content": {"store_id": "602"}},
        ],
        "candidate_search_complete": True,
        "ranking_method": "haversine",
        "source": "current_turn_tool_fact",
    }


def test_parallel_gate_rejects_location_capture_when_location_card_is_authoritative() -> None:
    selector_input = {
        "reply_chain_mode": "parallel_candidate_only",
        "authoritative_context": {
            "location_card": {
                "title": "萤火虫大厦",
                "coordinates": "24.535414,118.152077",
            }
        },
        "conversation_evidence": [],
        "content_assets": [
            {
                "content_id": "s10_store_prompt",
                "asset_role": "location_capture",
                "delivery_status": "available",
                "selection_constraints": {
                    "forbidden_when_authoritative_facts_present": ["location_card"]
                },
            }
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": "s10_store_prompt",
                "relevance": "direct",
                "evidence_purpose": "询问位置",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            }
        ]
    }

    assert _parallel_content_gate_output_violations(selector_output, selector_input) == [
        "candidate_asset_conflicts_with_authoritative_fact:s10_store_prompt:location_card"
    ]


def test_location_card_protocol_recovers_required_read_only_tools() -> None:
    state = {
        "shared_context": {
            "current_message": {
                "message_ref": "current_message",
                "message_type": "location",
            },
            "authoritative_facts": {
                "location_card": {
                    "coordinates": "24.535414,118.152077",
                    "title": "萤火虫大厦",
                    "address": "福建省厦门市湖里区岐山北二路1000号",
                }
            },
        }
    }

    calls = _protocol_required_read_only_tools(state)

    assert [item["name"] for item in calls] == [
        "customer_store_lookup",
        "distance_calculate",
    ]
    assert calls[0]["query"] == "福建省厦门市湖里区岐山北二路1000号 萤火虫大厦"
    assert calls[1]["origin"] == "24.535414,118.152077"
    assert all(item["evidence_refs"] == ["current_message"] for item in calls)


def test_location_card_protocol_facts_override_model_tool_arguments() -> None:
    planned = [
        {
            "name": "customer_store_lookup",
            "query": "萤火虫大厦",
            "evidence_refs": ["current_message"],
        },
        {
            "name": "distance_calculate",
            "origin": "model-invented-origin",
            "candidate_source": "customer_store_lookup",
        },
    ]
    required = [
        {
            "name": "customer_store_lookup",
            "query": "福建省厦门市湖里区岐山北二路1000号 萤火虫大厦",
            "purpose": "protocol_location_card_resolution",
            "evidence_refs": ["current_message"],
        },
        {
            "name": "distance_calculate",
            "origin": "24.535414,118.152077",
            "candidate_source": "customer_store_lookup",
            "purpose": "protocol_location_card_distance_ranking",
            "evidence_refs": ["current_message"],
        },
    ]

    merged = _merge_tool_calls(planned, required)

    assert merged == required


def test_plain_text_store_question_has_no_protocol_tool_recovery() -> None:
    state = {
        "shared_context": {
            "current_message": {
                "message_ref": "current_message",
                "message_type": "text",
            },
            "authoritative_facts": {"location_card": {}},
        }
    }

    assert _protocol_required_read_only_tools(state) == []


def _parallel_payment_validation_state(*, supporting_role: str = "customer") -> dict:
    state = {
        "content": "可以，给我发吧",
        "normalized_content": "可以，给我发吧",
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "shared_context": {
                "conversation": [
                    {
                        "message_ref": "offer_001",
                        "role": "assistant",
                        "content": "活动总价268元，活动范围和价值已经说明。",
                    },
                    {
                        "message_ref": "support_001",
                        "role": supporting_role,
                        "content": "门店位置我看过了。",
                    },
                ],
                "authoritative_facts": {
                    "sop_progress": {"completed_pack_ids": ["s10_activity_intro"]},
                    "orders_and_payment": {},
                },
                "content_indexes": {
                    "available_sop": {
                        "sop_packs": [
                            {
                                "content_id": "s10_activity_intro",
                                "asset_role": "activity_offer",
                            }
                        ]
                    }
                },
            },
        },
    }
    return _reply_validation_state(
        state,
        {
            "action": "payment",
            "payment_assessment": {
                "status": "payment_request",
                "evidence_refs": ["current_message"],
            },
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {
                "status": "unknown",
                "party_size": None,
                "evidence_refs": [],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": ["sop_completed:s10_activity_intro", "offer_001"],
                "supporting_key": "address",
                "supporting_refs": ["support_001"],
                "current_intent_refs": ["current_message"],
            },
        },
    )


def test_parallel_payment_accepts_prior_offer_customer_engagement_and_current_intent() -> None:
    state = _parallel_payment_validation_state()

    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": "可以，每位10元预约金，我把入口发您。"},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ],
        state,
    )


def test_parallel_message_normalization_preserves_model_payment_amount() -> None:
    state = _parallel_payment_validation_state()

    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "content": "两位一共20元预约金。"},
                {"type": "payment_collection", "content": {"amount": 20}},
            ]
        },
        state,
    )

    assert messages[1]["content"]["amount"] == 20


def test_parallel_multi_person_amount_requires_model_party_size_evidence() -> None:
    state = _parallel_payment_validation_state()

    with pytest.raises(ValueError, match="multi_person_payment_requires_known_party_size_assessment"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "两位一共20元预约金。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 20}},
            ],
            state,
        )


def test_parallel_payment_amount_must_match_model_party_size_assessment() -> None:
    state = _parallel_payment_validation_state()
    state["reply_party_size_assessment"] = {
        "status": "known",
        "party_size": 2,
        "evidence_refs": ["support_001"],
    }

    with pytest.raises(ValueError, match="payment_collection_amount_conflicts_with_party_size_assessment"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "两位每位10元预约金。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
            ],
            state,
        )


@pytest.mark.parametrize("status", ["manual_transfer", "unverified_paid_claim"])
def test_parallel_payment_assessment_blocks_card_for_non_card_payment_context(status: str) -> None:
    state = _parallel_payment_validation_state()
    state["reply_payment_assessment"] = {
        "status": status,
        "evidence_refs": ["current_message"],
    }

    with pytest.raises(ValueError, match=f"payment_assessment_blocks_payment_collection:{status}"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "我帮您核对。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
            ],
            state,
        )


def test_parallel_payment_card_requires_reply_owned_payment_request_assessment() -> None:
    state = _parallel_payment_validation_state()
    state["reply_payment_assessment"] = {"status": "unknown", "evidence_refs": []}

    with pytest.raises(ValueError, match="payment_collection_requires_payment_request_assessment"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "我把入口发您。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
            ],
            state,
        )


def test_parallel_unverified_paid_assessment_without_card_is_consistent() -> None:
    state = _parallel_payment_validation_state()
    state["reply_action"] = "ask"
    state["reply_payment_assessment"] = {
        "status": "unverified_paid_claim",
        "evidence_refs": ["current_message"],
    }
    state["reply_deposit_evidence"] = {
        "offer_prior_turn_refs": [],
        "supporting_key": "",
        "supporting_refs": [],
        "current_intent_refs": [],
    }

    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": "我先结合付款记录核对一下。"}],
        state,
    )


def test_parallel_payment_assessment_normalizes_unverified_oral_alias() -> None:
    state = _reply_validation_state(
        _parallel_state("已经转好了"),
        {
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_oral_paid_claim",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": [],
                "supporting_key": "",
                "supporting_refs": [],
                "current_intent_refs": [],
            },
        },
    )

    assert state["reply_payment_assessment"] == {
        "status": "unverified_paid_claim",
        "evidence_refs": ["current_message"],
    }


def test_parallel_payment_assessment_prunes_invalid_ref_when_valid_customer_ref_remains() -> None:
    state = _reply_validation_state(
        _parallel_state("已经转好了"),
        {
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_paid_claim",
                "evidence_refs": ["current_message", "conv_assistant_003"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": [],
                "supporting_key": "",
                "supporting_refs": [],
                "current_intent_refs": [],
            },
        },
    )

    assert state["reply_payment_assessment"]["evidence_refs"] == ["current_message"]


def test_parallel_payment_assessment_rejects_when_no_valid_customer_ref_remains() -> None:
    with pytest.raises(ValueError, match="payment_assessment_requires_customer_message_evidence"):
        _reply_validation_state(
            _parallel_state("已经转好了"),
            {
                "action": "ask",
                "payment_assessment": {
                    "status": "unverified_paid_claim",
                    "evidence_refs": ["conv_assistant_003"],
                },
                "deposit_evidence": {
                    "offer_prior_turn_refs": [],
                    "supporting_key": "",
                    "supporting_refs": [],
                    "current_intent_refs": [],
                },
            },
        )


def test_parallel_deposit_evidence_normalizes_none_supporting_key_to_empty() -> None:
    state = _reply_validation_state(
        _parallel_state("我直接转给你"),
        {
            "action": "ask",
            "payment_assessment": {
                "status": "manual_transfer",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": [],
                "supporting_key": "none",
                "supporting_refs": [],
                "current_intent_refs": [],
            },
        },
    )

    assert state["reply_deposit_evidence"]["supporting_key"] == ""


@pytest.mark.parametrize(
    "reply_text",
    [
        "可以转账，核对到就按预约金给您留活动名额。",
        "麻烦发付款成功截图，我确认后再继续给您登记。",
        "麻烦发付款截图，核对上了再继续给您登记。",
    ],
)
def test_parallel_manual_transfer_allows_conditional_post_verification_reservation(reply_text: str) -> None:
    state = _reply_validation_state(
        _parallel_state("不点小程序，转账吧"),
        {
            "action": "none",
            "payment_assessment": {
                "status": "manual_transfer",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": [],
                "supporting_key": "",
                "supporting_refs": [],
                "current_intent_refs": [],
            },
        },
    )

    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": reply_text,
            }
        ],
        state,
    )


def test_parallel_non_payment_action_ignores_ephemeral_deposit_audit_metadata() -> None:
    state = _parallel_payment_validation_state()
    state["reply_action"] = "offer"

    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": "我先把活动内容给您讲清楚。"}],
        state,
    )


def test_parallel_non_payment_action_accepts_empty_deposit_evidence() -> None:
    state = _parallel_payment_validation_state()
    state["reply_action"] = "offer"
    state["reply_deposit_evidence"] = {
        "offer_prior_turn_refs": [],
        "supporting_key": "",
        "supporting_refs": [],
        "current_intent_refs": [],
    }

    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": "我先把活动内容给您讲清楚。"}],
        state,
    )


def test_parallel_payment_accepts_prior_assistant_offer_ref_without_code_semantic_label() -> None:
    state = _parallel_payment_validation_state()
    state["reply_deposit_evidence"]["offer_prior_turn_refs"] = ["offer_001"]

    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": "可以，每位10元预约金，我把入口发您。"},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ],
        state,
    )


def test_parallel_payment_accepts_prior_assistant_offer_when_no_structured_completion_exists() -> None:
    state = _parallel_payment_validation_state()
    state["evidence_join"]["shared_context"]["authoritative_facts"]["sop_progress"] = {
        "completed_pack_ids": []
    }
    state["reply_deposit_evidence"]["offer_prior_turn_refs"] = ["offer_001"]

    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": "可以，每位10元预约金，我把入口发您。"},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ],
        state,
    )


def test_parallel_payment_rejects_supporting_key_without_prior_customer_engagement() -> None:
    state = _parallel_payment_validation_state(supporting_role="assistant")

    with pytest.raises(
        ValueError,
        match="payment_collection_requires_customer_engaged_supporting_key_evidence",
    ):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "可以，每位10元预约金，我把入口发您。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
            ],
            state,
        )


def test_parallel_payment_aggregates_missing_card_and_invalid_evidence_for_one_repair() -> None:
    state = _parallel_payment_validation_state(supporting_role="assistant")

    with pytest.raises(ValueError) as exc_info:
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": "可以，我把收款卡发您。"}],
            state,
        )

    error = str(exc_info.value)
    assert "parallel_reply_hard_violations::" in error
    assert "payment_action_requires_payment_collection" in error
    assert "payment_collection_requires_customer_engaged_supporting_key_evidence" in error


def test_parallel_chain_rejects_duplicate_payment_cards_instead_of_silently_deduping() -> None:
    state = _parallel_payment_validation_state()

    with pytest.raises(ValueError, match="duplicate_payment_collection_in_single_turn"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "每位10元预约金，我把入口发您。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
                {"type": "payment_collection", "order": 3, "content": {"amount": 10}},
            ],
            state,
        )


def test_parallel_chain_rejects_media_not_present_in_joined_evidence() -> None:
    state = _reply_validation_state(
        {
            **_parallel_state("有案例吗"),
            "evidence_join": {
                "schema_version": "reply_chain_evidence_join_v1",
                "content_candidates": [],
            },
        },
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {
                "status": "unknown",
                "party_size": None,
                "evidence_refs": [],
            },
        },
    )

    with pytest.raises(ValueError, match="unsupported_parallel_media_fact"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "我给您看一张真实案例。"},
                {"type": "image", "order": 2, "content": "https://example.invalid/invented.jpg"},
            ],
            state,
        )


def test_parallel_structural_preparation_does_not_rewrite_model_messages() -> None:
    original = [
        {"type": "image", "content": "https://example.invalid/invented.jpg"},
        {"type": "payment_collection", "content": {"amount": 10}},
        {"type": "payment_collection", "content": {"amount": 10}},
    ]
    warnings: list[dict] = []

    prepared = _prepare_structural_messages(
        original,
        {"evidence_join": {"schema_version": "reply_chain_evidence_join_v1"}},
        warnings,
    )

    assert [item["type"] for item in prepared] == [
        "image",
        "payment_collection",
        "payment_collection",
    ]
    assert warnings == []


def test_parallel_raw_schema_rejects_duplicate_payment_before_normalization() -> None:
    payload = {
        "reply_messages": [
            {"type": "text", "content": "每位10元预约金，我把入口发您。"},
            {"type": "payment_collection", "content": {"amount": 10}},
            {"type": "payment_collection", "content": {"amount": 10}},
        ],
        "used_fact_refs": [],
        "selected_content_ids": [],
        "action": "payment",
        "commit_actions": [],
    }

    with pytest.raises(ValueError, match="duplicate_payment_collection_in_single_turn"):
        _validate_parallel_raw_reply_schema(payload)


def test_parallel_raw_schema_rejects_silent_handoff_rewrites() -> None:
    base = {
        "action": "none",
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "commit_actions": [],
    }

    with pytest.raises(ValueError, match="invalid_parallel_reply_message_type"):
        _validate_parallel_raw_reply_schema(
            {
                **base,
                "reply_messages": [
                    {"type": "text", "content": "先回答客户"},
                    {"type": "human_handoff", "content": "需要人工关注"},
                ],
            }
        )

    with pytest.raises(ValueError, match="parallel_handoff_notice_must_follow_visible_messages"):
        _validate_parallel_raw_reply_schema(
            {
                **base,
                "reply_messages": [
                    {
                        "type": "human_handoff_notice",
                        "content": {"handoff_reason": "需要人工关注"},
                    },
                    {"type": "text", "content": "先回答客户"},
                ],
            }
        )

    with pytest.raises(ValueError, match="duplicate_human_handoff_notice_in_single_turn"):
        _validate_parallel_raw_reply_schema(
            {
                **base,
                "reply_messages": [
                    {"type": "text", "content": "先回答客户"},
                    {
                        "type": "human_handoff_notice",
                        "content": {"handoff_reason": "原因一"},
                    },
                    {
                        "type": "human_handoff_notice",
                        "content": {"handoff_reason": "原因二"},
                    },
                ],
            }
        )


def test_parallel_raw_schema_rejects_lossy_action_and_message_compatibility() -> None:
    base = {
        "reply_messages": [{"type": "text", "content": "收到。"}],
        "used_fact_refs": [],
        "selected_content_ids": [],
        "action": "continue_sales",
        "commit_actions": [],
    }
    with pytest.raises(ValueError, match="invalid_parallel_reply_action"):
        _validate_parallel_raw_reply_schema(base)

    base["action"] = "none"
    base["reply_messages"] = ["收到。"]
    with pytest.raises(ValueError, match="invalid_parallel_reply_message_object"):
        _validate_parallel_raw_reply_schema(base)


def test_parallel_raw_schema_accepts_external_media_url_shape_without_rewrite() -> None:
    payload = {
        "reply_messages": [
            {"type": "text", "content": "案例图：https://example.test/case.jpg"},
        ],
        "used_fact_refs": [],
        "selected_content_ids": [],
        "action": "none",
        "commit_actions": [],
    }

    with pytest.raises(ValueError, match="parallel_text_must_not_embed_image_url"):
        _validate_parallel_raw_reply_schema(payload)

    payload["reply_messages"] = [
        {"type": "image", "content": "https://example.test/case.jpg"},
    ]
    _validate_parallel_raw_reply_schema(payload)

    payload["reply_messages"] = [
        {"type": "image", "content": {"url": "https://example.test/case.jpg"}},
    ]
    _validate_parallel_raw_reply_schema(payload)


@pytest.mark.parametrize(
    ("message_type", "encoded_content", "expected_content"),
    [
        ("store_address", '{"store_id":"302"}', {"store_id": "302"}),
        (
            "payment_collection",
            '{"amount":10,"remark":"活动预约金"}',
            {"amount": 10, "remark": "活动预约金"},
        ),
    ],
)
def test_parallel_raw_schema_normalizes_json_string_for_structured_content(
    message_type: str,
    encoded_content: str,
    expected_content: dict[str, object],
) -> None:
    payload = {
        "reply_messages": [{"type": message_type, "content": encoded_content}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "payment" if message_type == "payment_collection" else "offer",
        "commit_actions": [],
    }

    _validate_parallel_raw_reply_schema(payload)

    assert payload["reply_messages"][0]["content"] == expected_content


@pytest.mark.parametrize("message_type", ["store_address", "payment_collection"])
def test_parallel_raw_schema_rejects_invalid_json_string_for_structured_content(
    message_type: str,
) -> None:
    payload = {
        "reply_messages": [{"type": message_type, "content": "not-json"}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "payment" if message_type == "payment_collection" else "offer",
        "commit_actions": [],
    }

    with pytest.raises(ValueError, match="invalid_parallel_reply_message_content"):
        _validate_parallel_raw_reply_schema(payload)


def test_parallel_message_normalization_does_not_apply_legacy_count_cap() -> None:
    payload = {
        "reply_messages": [
            {"type": "text", "content": f"消息{index}"}
            for index in range(1, 7)
        ],
        "used_fact_refs": [],
        "selected_content_ids": [],
        "action": "none",
        "commit_actions": [],
    }
    _validate_parallel_raw_reply_schema(payload)

    messages = validated_model_messages(
        payload,
        {"evidence_join": {"schema_version": "reply_chain_evidence_join_v1"}},
    )

    assert [item["content"] for item in messages] == [f"消息{index}" for index in range(1, 7)]


def test_assessment_reference_aliases_are_only_syntax_normalized() -> None:
    valid = {"current_message", "conv_002"}

    assert _canonical_assessment_refs(
        ["conversation:conv_002", "shared_context.current_message.content"], valid
    ) == ["conv_002", "current_message"]
    assert _canonical_assessment_refs(["recent_conversation:customer_refusal"], valid) == [
        "recent_conversation:customer_refusal"
    ]


def test_offer_amount_validation_rejects_tail_amount_as_deduction() -> None:
    text = "\u7ebf\u4e0a\u4ed810\u5143\u9884\u7ea6\u91d1\uff0c\u5230\u5e97\u518d\u62b5\u6263258\u5143\u3002"

    with pytest.raises(ValueError, match="offer_total_tail_amount_conflict"):
        validate_reply_consistency([{"type": "text", "order": 1, "content": text}], {"evidence_join": {}})


@pytest.mark.parametrize("status", ["health_risk", "complaint_refund", "explicit_reject"])
def test_parallel_hard_safety_assessment_blocks_payment_card(status: str) -> None:
    state = _reply_validation_state(
        _parallel_state("当前客户原话"),
        {
            "action": "payment",
            "safety_assessment": {"status": status, "evidence_refs": ["current_message"]},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "content": "我先按您当前情况处理。"},
                {"type": "payment_collection", "content": {"amount": 10}},
            ]
        },
        state,
    )

    with pytest.raises(ValueError, match=f"payment_collection_blocked_by_{status}"):
        validate_reply_consistency(messages, state)


def test_parallel_unknown_party_size_does_not_use_python_keyword_inference() -> None:
    state = _reply_validation_state(
        _parallel_state("我们五个人一起"),
        {
            "action": "payment",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    assert payment_collection_context(state=state, messages=[]) == {
        "participants": 1,
        "amount": 10,
        "over_limit": False,
    }


def test_parallel_payment_action_requires_matching_payment_structure() -> None:
    state = _reply_validation_state(
        _parallel_state("怎么付费"),
        {
            "action": "payment",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "可以，我把收款发您。"}]},
        state,
    )

    with pytest.raises(ValueError, match="payment_action_requires_payment_collection"):
        validate_reply_consistency(messages, state)


def test_parallel_registration_action_requires_authoritative_paid_fact() -> None:
    state = _reply_validation_state(
        _parallel_state("可以，给我预约"),
        {
            "action": "registration",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "把姓名和电话发我登记。"}]},
        state,
    )

    with pytest.raises(ValueError, match="registration_action_requires_paid_context"):
        validate_reply_consistency(messages, state)


def test_parallel_visible_registration_language_is_deferred_to_fact_audit() -> None:
    state = _reply_validation_state(
        _parallel_state("活动怎么参加"),
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "您想登记的话，把姓名发我就行。"}],
        state,
    )


@pytest.mark.parametrize(
    "reply_text",
    [
        "您要登记的话，直接回我名字+手机号。",
        "活动名额我先帮您登记。",
        "我先给您保留活动名额。",
        "可以，先帮您登记活动名额。",
        "可以，您发登记就行，我先帮您记上这个活动名额。",
        "可以，我先把这个活动名额给您留意上。",
        "直接回复“登记”就行。",
    ],
)
def test_parallel_registration_claim_variants_are_not_parsed_by_python(
    reply_text: str,
) -> None:
    state = _reply_validation_state(
        _parallel_state("活动怎么参加"),
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency([{"type": "text", "content": reply_text}], state)


def test_parallel_future_post_payment_registration_instruction_is_not_blocked() -> None:
    state = _reply_validation_state(
        _parallel_state("活动怎么参加"),
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "您支付后再把姓名发我登记就行。"}],
        state,
    )


def test_parallel_future_registration_after_payment_verification_is_not_blocked() -> None:
    state = _reply_validation_state(
        _parallel_state("已经转好了"),
        {
            "action": "ask",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "您把截图发我，确认到账后我再给您登记。"}],
        state,
    )


def test_parallel_reply_repair_context_includes_exact_candidate_delivery_requirements() -> None:
    state = {
        **_parallel_state("活动名额怎么登记"),
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "shared_context": _parallel_state("活动名额怎么登记")["shared_context"],
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "asset_role": "activity_offer",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "text", "content": "活动价268元。"},
                        {"type": "image", "content": "https://example.invalid/activity.jpg"},
                    ],
                }
            ],
        },
    }

    context = _parallel_reply_repair_context(state)

    assert context["content_candidate_delivery_requirements"] == [
        {
            "content_id": "s10_activity_intro",
            "required_used_fact_ref": "content_asset:s10_activity_intro",
            "asset_role": "activity_offer",
            "delivery_status": "available",
            "repeat_delivery_required": True,
            "messages": [
                {"type": "text", "content": "活动价268元。"},
                {"type": "image", "content": "https://example.invalid/activity.jpg"},
            ],
        }
    ]
    assert context["content_candidate_reference_options"] == [
        {
            "content_id": "s10_activity_intro",
            "used_fact_ref": "content_asset:s10_activity_intro",
        }
    ]
    assert context["authoritative_fact_reference_options"] == []
    assert context["valid_customer_message_refs"] == ["current_message"]


def test_parallel_reply_repair_context_exposes_authoritative_payment_reference() -> None:
    state = _parallel_state("张三 13800138000")
    shared_context = {
        "current_message": {
            "message_ref": "current_message",
            "content": "张三 13800138000",
        },
        "conversation": [
            {
                "message_ref": "current_message",
                "role": "customer",
                "content": "张三 13800138000",
            }
        ],
        "authoritative_facts": {
            "orders_and_payment": {
                "resolved_payment": {
                    "deposit_state": "paid_by_platform_transfer_event",
                }
            }
        },
    }
    state["shared_context"] = shared_context
    state["evidence_join"] = {
        "schema_version": "reply_chain_evidence_join_v1",
        "shared_context": shared_context,
        "content_candidates": [],
        "tool_facts": {},
    }

    context = _parallel_reply_repair_context(state)

    assert context["authoritative_fact_reference_options"] == [
        {"ref": "payment_fact:authoritative_paid", "kind": "paid_deposit"}
    ]


def test_parallel_reply_repair_rechecks_root_action_before_fixing_structure() -> None:
    messages = _reply_retry_messages(
        [{"role": "system", "content": "system"}],
        ValueError("invalid_parallel_reply_message_object"),
        previous_payload={"reply_messages": [{"type": "text", "content": "原回复"}]},
        validation_context={"allowed_selected_content_ids": ["s10_activity_intro"]},
    )

    instruction = messages[-1]["content"]
    assert "完整一致性修复，不是机械追加缺失字段" in instruction
    assert "先重新核对上一版 action 是否被本轮真实引用和硬事实允许" in instruction
    assert "若动作本身不合法，必须撤销或改成真实动作" in instruction
    assert "不要因为错误写着‘缺卡片’就直接加卡" in instruction
    assert "每个 selected_content_ids 都必须在 used_fact_refs" in instruction
    assert "不能用 sop_completed:<id>" in instruction
    assert "action=registration 只能用于 authoritative_paid=true" in instruction


def test_parallel_structural_repair_guard_exposes_exact_customer_ref_choices() -> None:
    guard = _reply_structural_repair_guard(
        "payment_collection_requires_customer_engaged_supporting_key_evidence",
        previous_payload={
            "action": "payment",
            "payment_assessment": {
                "status": "payment_request",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "supporting_key": "effect",
                "supporting_refs": ["conv_002"],
            },
        },
        validation_context={
            "prior_customer_message_refs": ["conv_001"],
            "prior_message_options": [
                {"ref": "conv_001", "role": "customer", "content": "我这个斑有五六年了。"},
                {"ref": "conv_002", "role": "assistant", "content": "我先给您看效果。"},
            ],
        },
    )

    assert "parallel_reply_structural_repair_v1" in guard
    assert '"previous_supporting_key":"effect"' in guard
    assert '"ref":"conv_001"' in guard
    assert "我这个斑有五六年了" in guard
    assert '"ref":"conv_002"' not in guard
    assert "没有任何语义匹配的客户原话" in guard
    assert "主动提问" in guard
    assert "不得因为客户原话是疑问句" in guard
    assert "payment_request_decision_must_remain_structurally_consistent" not in guard
    assert '"choice_cancel_payment"' in guard
    assert "没有任何语义匹配的客户原话" in guard


def test_parallel_structural_repair_guard_preserves_tool_store_delivery() -> None:
    guard = _reply_structural_repair_guard(
        "completed_content_repeat_requires_current_customer_ref",
        previous_payload={
            "selected_content_ids": ["s10_store_prompt"],
            "used_fact_refs": ["content_asset:s10_store_prompt"],
            "reply_messages": [{"type": "text", "content": "您在哪个区呢？"}],
        },
        validation_context={
            "structured_delivery_options": {
                "store_address": {
                    "status": "ready",
                    "message_payloads": [
                        {"type": "store_address", "content": {"store_id": "241"}},
                        {"type": "store_address", "content": {"store_id": "242"}},
                    ],
                }
            }
        },
    )

    assert "current_tool_store_delivery_requires_explicit_decision" in guard
    assert '"store_id":"241"' in guard
    assert '"store_id":"242"' in guard
    assert "明确选择 deliver 或 defer" in guard
    assert "completed_content_repeat_reference" in guard


def test_parallel_structural_repair_guard_fixes_unverified_reservation_wording_only() -> None:
    guard = _reply_structural_repair_guard(
        "registration_confirmation_fact_required",
        previous_payload={
            "action": "payment",
            "payment_assessment": {"status": "payment_request", "evidence_refs": ["current_message"]},
            "deposit_evidence": {
                "offer_prior_turn_refs": ["conv_003"],
                "supporting_key": "effect",
                "supporting_refs": ["conv_001"],
                "current_intent_refs": ["current_message"],
            },
            "selected_content_ids": ["s10_deposit_close"],
            "used_fact_refs": ["current_message", "content_asset:s10_deposit_close"],
            "reply_messages": [
                {"type": "text", "content": "可以，先给您留着。"},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
        },
        validation_context={},
    )

    assert "unverified_registration_or_appointment_wording" in guard
    assert "只修正客户可见 text" in guard
    assert '"type":"payment_collection"' in guard
    assert "不得重新审理销售动作" in guard


def test_parallel_structural_repair_guard_cleans_manual_transfer_without_echoing() -> None:
    guard = _reply_structural_repair_guard(
        "invalid_reply_deposit_supporting_key",
        previous_payload={
            "action": "payment",
            "payment_assessment": {
                "status": "manual_transfer",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": ["conv_001"],
                "supporting_key": "activity",
                "supporting_refs": ["conv_001"],
                "current_intent_refs": ["current_message"],
            },
            "selected_content_ids": ["s10_deposit_close"],
        },
        validation_context={"content_candidate_delivery_requirements": []},
    )

    assert "non_card_payment_status_requires_structural_cleanup" in guard
    assert '"payment_status":"manual_transfer"' in guard
    assert "不能原样复制 current_message" in guard
    assert '"payment_collection":"禁止"' in guard


def test_parallel_structural_repair_guard_requires_full_asset_or_deselect() -> None:
    guard = _reply_structural_repair_guard(
        "selected_content_delivery_missing:content_id=s10_deposit_close",
        previous_payload={
            "selected_content_ids": ["s10_deposit_close"],
        },
        validation_context={
            "content_candidate_delivery_requirements": [
                {
                    "content_id": "s10_deposit_close",
                    "required_used_fact_ref": "content_asset:s10_deposit_close",
                    "messages": [
                        {"type": "image", "content": "https://example.invalid/deposit.jpg"},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                },
                {
                    "content_id": "s10_activity_intro",
                    "required_used_fact_ref": "content_asset:s10_activity_intro",
                    "messages": [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
                },
            ],
        },
    )

    assert '"content_id":"s10_deposit_close"' in guard
    assert "https://example.invalid/deposit.jpg" in guard
    assert '"amount":10' in guard
    assert "https://example.invalid/activity.jpg" not in guard
    assert "逐项输出 exact_delivery_requirements.messages 中全部结构消息" in guard
    assert "删除该 ID 及其 content_asset:<id> 引用" in guard


def test_parallel_structural_repair_guard_preserves_selected_asset_on_unrelated_repair() -> None:
    guard = _reply_structural_repair_guard(
        "offer_total_tail_amount_conflict",
        previous_payload={
            "action": "payment",
            "payment_assessment": {"status": "payment_request"},
            "selected_content_ids": ["s10_deposit_close"],
            "deposit_evidence": {
                "offer_prior_turn_refs": ["conv_003"],
                "supporting_key": "effect",
                "supporting_refs": ["conv_001"],
                "current_intent_refs": ["current_message"],
            },
            "reply_messages": [
                {"type": "text", "content": "到店抵扣258元"},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
        },
        validation_context={
            "content_candidate_delivery_requirements": [
                {
                    "content_id": "s10_deposit_close",
                    "required_used_fact_ref": "content_asset:s10_deposit_close",
                    "messages": [
                        {"type": "image", "content": "https://example.invalid/deposit.jpg"},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                }
            ],
        },
    )

    assert "selected_content_delivery_incomplete" in guard
    assert "preserve_or_cancel_payment_structure_as_one_group" in guard
    assert "https://example.invalid/deposit.jpg" in guard
    assert '"previous_payment_status":"payment_request"' in guard
    assert "不得只修文字后丢卡" in guard


def test_parallel_tail_amount_repair_locks_non_text_payment_structure() -> None:
    guard = _reply_structural_repair_guard(
        "offer_total_tail_amount_conflict",
        previous_payload={
            "action": "payment",
            "payment_assessment": {"status": "payment_request", "evidence_refs": ["current_message"]},
            "deposit_evidence": {
                "offer_prior_turn_refs": ["conv_003"],
                "supporting_key": "effect",
                "supporting_refs": ["conv_001"],
                "current_intent_refs": ["current_message"],
            },
            "selected_content_ids": ["s10_deposit_close"],
            "used_fact_refs": ["current_message", "content_asset:s10_deposit_close"],
            "reply_messages": [
                {"type": "text", "content": "到店抵扣258元"},
                {"type": "image", "content": "https://example.invalid/deposit.jpg"},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
        },
        validation_context={
            "content_candidate_delivery_requirements": [
                {
                    "content_id": "s10_deposit_close",
                    "required_used_fact_ref": "content_asset:s10_deposit_close",
                    "messages": [
                        {"type": "image", "content": "https://example.invalid/deposit.jpg"},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                }
            ]
        },
    )

    assert "text_only_tail_amount_wording_repair" in guard
    assert "只允许修正客户可见 text" in guard
    assert '"action":"payment"' in guard
    assert "https://example.invalid/deposit.jpg" in guard
    assert '"amount":10' in guard
    assert "不得删除或新增结构消息" in guard


def test_parallel_structural_repair_guard_is_last_repair_contract() -> None:
    messages = _reply_retry_messages(
        [{"role": "system", "content": "FULL SALES PROMPT MUST NOT BE REPEATED"}],
        ValueError(
            "selected_content_delivery_missing:content_id=s10_deposit_close;;"
            "payment_collection_requires_customer_engaged_supporting_key_evidence"
        ),
        previous_payload={
            "selected_content_ids": ["s10_deposit_close"],
            "deposit_evidence": {"supporting_key": "effect", "supporting_refs": ["conv_002"]},
        },
        validation_context={
            "current_message": {"content": "那我参加", "message_type": "text"},
            "prior_customer_message_refs": ["conv_001"],
            "prior_message_options": [
                {"ref": "conv_001", "role": "customer", "content": "有五六年了"}
            ],
            "content_candidate_delivery_requirements": [
                {
                    "content_id": "s10_deposit_close",
                    "required_used_fact_ref": "content_asset:s10_deposit_close",
                    "messages": [{"type": "payment_collection", "content": {"amount": 10}}],
                }
            ],
        },
    )

    instruction = messages[-1]["content"]
    checklist_at = instruction.rindex("最高优先级最终结构清单")
    output_at = instruction.rindex("请只输出修复后的完整严格 JSON 对象")
    assert checklist_at < output_at
    assert "本次只修复校验器明确指出的结构或事实表达错误" in instruction
    assert "然后再按以下通用一致性合同复核整份输出" not in instruction
    assert "missing_customer_engagement_reference" in instruction[checklist_at:output_at]
    assert "selected_content_delivery_incomplete" in instruction[checklist_at:output_at]
    assert len(messages) == 4
    assert "JSON 结构修复器" in messages[0]["content"]
    assert "FULL SALES PROMPT MUST NOT BE REPEATED" not in str(messages)
    assert "那我参加" in messages[1]["content"]
    assert messages[2]["role"] == "assistant"


def test_payment_repair_distinguishes_prior_activity_from_current_asset() -> None:
    hint = _reply_repair_hint("payment_action_requires_payment_collection")

    assert "当前轮的 content_asset:<id> 不能冒充更早活动证据" in hint
    assert "repair 必须保留 payment 并补齐卡片" in hint
    assert "若更早活动引用无效" in hint


def test_health_safety_reference_repair_is_citation_only() -> None:
    hint = _reply_repair_hint("safety_assessment_has_invalid_evidence_ref")

    assert "citation-only repair" in hint
    assert "do not newly select a content asset" in hint
    assert "add deposit_evidence" in hint


def test_deposit_evidence_repair_does_not_recharge_unverified_paid_claim() -> None:
    hint = _reply_repair_hint("deposit_evidence_requires_payment_action")

    assert "普通文字声称‘付好了/转好了’" in hint
    assert "绝对不能重新发卡" in hint
    assert "不能进入 registration" in hint
    assert '"current_intent_refs":[]' in hint
    assert "不要在 current_intent_refs 中保留 current_message" in hint
    assert "selected_content_ids 精确清空为 []" in hint
    assert "不交付候选中的图片或 payment_collection" in hint


def test_unpaid_registration_repair_preserves_non_card_payment_channel() -> None:
    hint = _reply_repair_hint("unpaid_registration_claim_before_payment")

    assert "只修正未付状态措辞" in hint
    assert "必须保留原支付通道" in hint
    assert "绝对不能改成 payment_request" in hint


def test_deposit_supporting_key_repair_rechecks_specific_payment_channel_first() -> None:
    hint = _reply_repair_hint("invalid_reply_deposit_supporting_key")

    assert "信息特异性" in hint
    assert "待核对声明" in hint
    assert "manual_transfer" in hint
    assert "都优先于一般 payment_request" in hint


def test_reply_repair_locks_declared_unverified_paid_claim_to_non_card_path() -> None:
    messages = _reply_retry_messages(
        [{"role": "system", "content": "output json"}],
        ValueError("deposit_evidence_requires_payment_action"),
        previous_payload={
            "reply_messages": [{"type": "text", "content": "请发截图核对。"}],
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_paid_claim",
                "evidence_refs": ["current_message"],
            },
        },
    )

    instruction = messages[-1]["content"]
    assert "必须保留这一更具体的非小程序支付判断" in instruction
    assert "selected_content_ids=[]" in instruction
    assert "不发送 payment_collection" in instruction


def test_reply_repair_rechecks_generic_payment_request_against_original_message() -> None:
    messages = _reply_retry_messages(
        [{"role": "system", "content": "output json"}],
        ValueError("invalid_reply_deposit_supporting_key"),
        previous_payload={
            "reply_messages": [{"type": "text", "content": "直接转账即可。"}],
            "action": "payment",
            "payment_assessment": {
                "status": "payment_request",
                "evidence_refs": ["current_message"],
            },
        },
    )

    instruction = messages[-1]["content"]
    assert "枚举合法不代表语义一定正确" in instruction
    assert "客户选择人工转账或不用小程序应改为 manual_transfer" in instruction
    assert "代码没有替你做关键词判定" in instruction
    assert "必须在同一个 JSON 中成组完成四项结构修复" in instruction
    assert "不得只改 payment_assessment 枚举却保留发卡结构" in instruction
    assert instruction.count("最高优先级结构要求") == 1


def test_missing_supporting_evidence_repair_preserves_payment_assessment() -> None:
    hint = _reply_repair_hint("payment_collection_requires_prior_supporting_key_evidence")

    assert "必须先由你重新阅读 current_message 核对支付位置" in hint
    assert "若上一版把更具体的支付位置误归成 payment_request" in hint
    assert "只有复核后仍是一般付款请求或明确索要收款卡" in hint
    assert "必须保留 payment_request 和支付通道" in hint
    assert "不得把‘把收款卡发我/发卡给我’改写成人工转账" in hint
    assert "不得重新审理该业务结论" in hint
    assert "追加到原 supporting_refs" in hint
    assert "确实不存在任何与原 supporting_key 对应" in hint


def test_combined_asset_and_supporting_evidence_repair_preserves_payment_channel() -> None:
    hint = _reply_repair_hint(
        "selected_content_delivery_missing:content_id=s10_deposit_close;required=image:x;;"
        "payment_collection_requires_customer_engaged_supporting_key_evidence"
    )

    assert "先重新阅读 current_message" in hint
    assert "人工转账选择优先于一般 payment_request" in hint
    assert "只有复核后仍是一般付款请求或明确索要收款卡" in hint
    assert "不要把真实索要收款卡误改成人工转账" in hint
    assert "prior_message_options" in hint


def test_nearby_store_repair_does_not_create_optional_store_step() -> None:
    hint = _reply_repair_hint("nearby_store_claim_without_location_fact")

    assert "只删除整条可选门店承诺" in hint
    assert "如果客户当前确实要求匹配门店" in hint
    assert "不要仅因 Gate 提名了门店资产" in hint


def test_parallel_reply_health_policy_distinguishes_assessment_from_operation() -> None:
    assert "当前明确健康风险" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得发送预约金卡" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "可以只回答或暂停" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_amount_conflict_repair_rejects_tail_amount_as_deduction() -> None:
    hint = _reply_repair_hint("offer_total_tail_amount_conflict")

    assert "不能写成‘到店抵扣258元’" in hint

def test_parallel_registration_claim_text_is_not_decided_from_legacy_profile_by_python() -> None:
    state = _reply_validation_state(
        {
            **_parallel_state("我还没付"),
            "customer_basic_info": {
                "deposit_state": {"status": "paid_by_screenshot"},
            },
        },
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "已经给您报名好了。"}]},
        state,
    )

    validate_reply_consistency(messages, state)


def test_parallel_unpaid_completion_claim_is_deferred_to_fact_audit() -> None:
    state = _reply_validation_state(
        _parallel_state("可以先把活动留着"),
        {
            "action": "none",
            "payment_assessment": {"status": "none", "evidence_refs": []},
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "content": "可以，活动我先给您留住。"},
            ]
        },
        state,
    )

    validate_reply_consistency(messages, state)


def test_parallel_conditional_and_completed_language_is_not_regex_parsed() -> None:
    state = _reply_validation_state(
        _parallel_state("可以先把活动留着"),
        {
            "action": "none",
            "payment_assessment": {"status": "none", "evidence_refs": []},
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {
            "reply_messages": [
                {
                    "type": "text",
                    "content": "可以，先帮您把活动留着。需要的话我现在就发预约金卡，付了就能保留名额。",
                },
            ]
        },
        state,
    )

    validate_reply_consistency(messages, state)


def test_parallel_paid_registration_completion_language_is_fact_audited_not_regex_parsed() -> None:
    state = _parallel_state("张三 13800138000")
    state["shared_context"]["authoritative_facts"] = {"orders_and_payment": {
        "resolved_payment": {"deposit_state": "paid_by_platform_transfer_event"}
    }}
    state = _reply_validation_state(
        state,
        {
            "action": "registration",
            "payment_assessment": {
                "status": "authoritative_paid",
                "evidence_refs": ["payment_fact:authoritative_paid"],
            },
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "收到，已经给您登记好了。"}],
        state,
    )


def test_parallel_authoritative_paid_fact_cannot_be_downgraded_to_unverified_claim() -> None:
    state = _parallel_state("付好了")
    state["shared_context"]["authoritative_facts"] = {"orders_and_payment": {
        "resolved_payment": {"deposit_state": "paid_by_platform_transfer_event"}
    }}
    state = _reply_validation_state(
        state,
        {
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_paid_claim",
                "evidence_refs": ["current_message"],
            },
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    with pytest.raises(
        ValueError,
        match="authoritative_paid_fact_requires_matching_payment_assessment",
    ):
        validate_reply_consistency(
            [{"type": "text", "content": "收到，我先核对一下。"}],
            state,
        )


def test_parallel_existing_order_fact_allows_completed_registration_claim() -> None:
    state = _parallel_state("我之前已经登记过了")
    state["fact_envelope"] = {
        "structured_facts": {
            "order_facts": [{"order_id": "order-1", "status": "created"}],
        }
    }
    state = _reply_validation_state(
        state,
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "是的，您的活动登记已经完成。"}],
        state,
    )


def test_parallel_existing_appointment_record_does_not_block_deposit_card() -> None:
    state = _parallel_payment_validation_state()
    state["fact_envelope"] = {
        "structured_facts": {
            "appointment_facts": [
                {"type": "appointment_created", "status": "created", "appointment_id": "appt-1"}
            ]
        }
    }

    validate_reply_consistency(
        [
            {"type": "text", "content": "可以，每位10元预约金，我把入口发您。"},
            {"type": "payment_collection", "content": {"amount": 10}},
        ],
        state,
    )


def test_parallel_appointment_claim_is_fact_audited_not_regex_parsed() -> None:
    state = _reply_validation_state(
        {**_parallel_state("那就明天下午"), "appointment_id": "legacy-appt"},
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "好的，明天下午已经给您安排好了。"}],
        state,
    )


def test_parallel_joined_appointment_fact_allows_confirmation_claim() -> None:
    state = _parallel_state("那就明天下午")
    state["fact_envelope"] = {
        "structured_facts": {
            "appointment_facts": [
                {
                    "type": "appointment_confirmed",
                    "appointment_id": "appt-2",
                    "appointment_time": "2026-08-11 14:00:00",
                }
            ]
        }
    }
    state = _reply_validation_state(
        state,
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "好的，明天下午已经给您安排好了。"}],
        state,
    )


def test_parallel_future_registration_language_is_not_reclassified_as_completed_fact() -> None:
    state = _reply_validation_state(
        _parallel_state("那怎么报名"),
        {
            "action": "ask",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "付好后跟我说一声，我给您登记活动。"}]},
        state,
    )

    validate_reply_consistency(messages, state)


def test_parallel_health_recovery_store_visit_is_not_an_available_time_claim() -> None:
    state = _reply_validation_state(
        _parallel_state("脸上还在过敏"),
        {
            "action": "none",
            "safety_assessment": {"status": "health_risk", "evidence_refs": ["current_message"]},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "content": "您先等皮肤稳定下来，之后可以先到店检测，再判断是否适合操作。"}
            ]
        },
        state,
    )

    validate_reply_consistency(messages, state)


def test_parallel_non_card_payment_explanation_can_use_offer_action() -> None:
    state = _reply_validation_state(
        _parallel_state("预约金是怎么抵扣的"),
        {
            "action": "offer",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "每位10元预约金，到店操作时直接抵扣。"}]},
        state,
    )

    validate_reply_consistency(messages, state)


def test_gate_candidate_images_are_authorized_facts_for_reply() -> None:
    state = {
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_need_and_case",
                    "messages": [
                        {"type": "image", "content": "https://example.test/case.jpg"},
                    ],
                }
            ]
        }
    }

    assert "https://example.test/case.jpg" in _case_image_urls(state)


def test_parallel_store_lookup_fact_does_not_force_model_to_send_card() -> None:
    state = _reply_validation_state(
        {
            **_parallel_state("\u6b66\u5e73"),
            "tool_plan": {"tool_calls": [{"name": "customer_store_lookup"}]},
            "fact_envelope": {
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "send_single",
                        "delivery_store_ids": ["601"],
                    },
                    "store_facts": [{"store_id": "601", "store_fact_integrity": "valid"}],
                }
            },
        },
        {
            "action": "ask",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency([{"type": "text", "content": "我先把您问的情况说明清楚。"}], state)
    validate_reply_consistency([{"type": "text", "content": "位置我发您。"}], state)

    validate_reply_consistency([{"type": "store_address", "content": {"store_id": "601"}}], state)


def test_parallel_reply_using_current_store_lookup_must_deliver_resolved_cards() -> None:
    base = {
        **_parallel_state("浦东"),
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "send_multiple",
                    "delivery_store_ids": ["405", "152"],
                },
                "store_facts": [
                    {"store_id": "405", "store_fact_integrity": "valid"},
                    {"store_id": "152", "store_fact_integrity": "valid"},
                ],
            }
        },
    }
    base["evidence_join"]["tool_facts"] = {
        "customer_store_lookup": {
            "store_resolution_fact": {
                "status": "send_multiple",
                "delivery_store_ids": ["405", "152"],
            }
        }
    }
    state = _reply_validation_state(
        base,
        {
            "action": "ask",
            "used_fact_refs": ["current_message", "tool_fact:customer_store_lookup"],
            "structured_delivery_decisions": [
                {
                    "fact_ref": "tool_fact:customer_store_lookup",
                    "decision": "deliver",
                    "reason": "客户正在索要门店位置",
                }
            ],
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    with pytest.raises(
        ValueError,
        match="planned_store_lookup_requires_store_delivery:required_store_ids=152,405",
    ):
        validate_reply_consistency(
            [{"type": "text", "content": "浦东这边有两家，我把位置发您。"}],
            state,
        )

    validate_reply_consistency(
        [
            {"type": "text", "content": "浦东这边有两家，位置都发您。"},
            {"type": "store_address", "content": {"store_id": "405"}},
            {"type": "store_address", "content": {"store_id": "152"}},
        ],
        state,
    )


def test_parallel_reply_may_explicitly_defer_store_delivery_without_code_overriding_sales_judgment() -> None:
    base = {
        **_parallel_state("先别发地址，我还在工作"),
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "send_single",
                    "delivery_store_ids": ["405"],
                },
                "store_facts": [{"store_id": "405", "store_fact_integrity": "valid"}],
            }
        },
    }
    base["evidence_join"]["tool_facts"] = {
        "customer_store_lookup": {
            "store_resolution_fact": {
                "status": "send_single",
                "delivery_store_ids": ["405"],
            }
        }
    }
    state = _reply_validation_state(
        base,
        {
            "action": "none",
            "used_fact_refs": ["current_message"],
            "structured_delivery_decisions": [
                {
                    "fact_ref": "tool_fact:customer_store_lookup",
                    "decision": "defer",
                    "reason": "客户明确要求当前不要发送地址",
                }
            ],
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency(
        [{"type": "text", "content": "好，您先忙，方便时再联系我就行。"}],
        state,
    )


def test_parallel_location_card_with_resolved_store_requires_structured_delivery() -> None:
    state = _reply_validation_state(
        {
            **_parallel_state("定位卡片：萤火虫大厦"),
            "shared_context": {
                "current_message": {
                    "message_ref": "current_message",
                    "role": "customer",
                    "content": "定位卡片：萤火虫大厦",
                    "message_type": "location",
                },
                "conversation": [],
                "authoritative_facts": {
                    "location_card": {
                        "coordinates": "24.535414,118.152077",
                        "title": "萤火虫大厦",
                        "address": "福建省厦门市湖里区岐山北二路1000号",
                    }
                },
            },
            "fact_envelope": {
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "send_single",
                        "delivery_store_ids": ["601"],
                    },
                    "store_facts": [{"store_id": "601", "store_fact_integrity": "valid"}],
                }
            },
        },
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    with pytest.raises(ValueError, match="planned_store_lookup_requires_store_delivery:required_store_ids=601"):
        validate_reply_consistency([{"type": "text", "content": "对应的是厦门百星湖里店。"}], state)

    validate_reply_consistency(
        [{"type": "store_address", "content": {"store_id": "601"}}],
        state,
    )


def test_parallel_pronoun_only_claim_is_deferred_to_fact_audit() -> None:
    state = _reply_validation_state(
        _parallel_state("可以先把活动留着"),
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency([{"type": "text", "content": "好的，先帮您留着。"}], state)


@pytest.mark.parametrize(
    "content",
    [
        "可以，给您先留活动名额。",
        "可以，先给您留活动名额。",
        "可以，先帮您留活动名额。",
        "好的，给您留着这个活动名额。",
        "行，给您先保留本次活动名额。",
    ],
)
def test_parallel_claim_variants_are_deferred_to_fact_audit(content: str) -> None:
    state = _reply_validation_state(
        _parallel_state("可以先把活动留着"),
        {
            "action": "none",
            "safety_assessment": {"status": "none", "evidence_refs": []},
            "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        },
    )

    validate_reply_consistency([{"type": "text", "content": content}], state)


def test_parallel_reply_schema_normalizes_omitted_optional_commit_actions() -> None:
    payload = {
        "reply_messages": [{"type": "text", "content": "收到。"}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "none",
        "commit_actions": None,
    }

    _validate_parallel_raw_reply_schema(payload)

    assert payload["commit_actions"] == []


def test_parallel_store_delivery_repair_hint_names_authoritative_ids() -> None:
    hint = _reply_repair_hint(
        "planned_store_lookup_requires_store_delivery:required_store_ids=601,602"
    )

    assert "required_store_ids" in hint
    assert "store_address" in hint
    assert '"store_id":"真实ID"' in hint
    assert "不得增加其他门店" in hint


def test_tool_planner_calls_require_real_evidence_refs() -> None:
    calls, violations = _normalize_read_only_tool_calls(
        [
            {
                "name": "customer_store_lookup",
                "arguments": {"query": "洪湖市"},
                "evidence_refs": ["current_message"],
            },
            {
                "name": "kb_search",
                "arguments": {"kb_name": "case_studies", "query": "效果怎么样"},
                "evidence_refs": ["hallucinated_ref"],
            },
        ],
        valid_evidence_refs={"current_message"},
    )

    assert [item["name"] for item in calls] == ["customer_store_lookup"]
    assert violations == ["tool_call_invalid_evidence_ref:kb_search"]


def test_tool_planner_evidence_field_paths_are_syntax_normalized() -> None:
    calls, violations = _normalize_read_only_tool_calls(
        [
            {
                "name": "customer_store_lookup",
                "arguments": {"query": "武平"},
                "evidence_refs": ["current_message.content", "conversation.conv_001.content"],
            }
        ],
        valid_evidence_refs={"current_message", "conv_001"},
    )

    assert violations == []
    assert calls[0]["evidence_refs"] == ["current_message", "conv_001"]


def test_tool_planner_rejects_missing_required_read_only_arguments() -> None:
    calls, violations = _normalize_read_only_tool_calls(
        [
            {
                "name": "kb_search",
                "arguments": {"kb_name": "case_studies"},
                "evidence_refs": ["current_message"],
            },
            {
                "name": "customer_store_lookup",
                "arguments": {"purpose": "lookup"},
                "evidence_refs": ["current_message"],
            },
        ],
        valid_evidence_refs={"current_message"},
    )

    assert calls == []
    assert violations == [
        "tool_call_missing_argument:kb_search:query",
        "tool_call_missing_location_argument:customer_store_lookup",
    ]


def test_tool_planner_prompt_requires_distance_for_parent_scope_location_fallback() -> None:
    prompt = parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT

    assert "同时规划 `customer_store_lookup` 与 `distance_calculate`" in prompt
    assert "不要求客户必须先说“最近”" in prompt


def test_tool_planner_prompt_composes_recent_parent_city_with_current_district() -> None:
    prompt = parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT

    assert "广州市番禺区" in prompt
    assert "证据同时引用当前消息和历史中的“广州”" in prompt
    assert "这不是普通短确认" in prompt


def test_tool_planner_prompt_forbids_inventing_parent_for_raw_village_name() -> None:
    prompt = parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT

    assert "乌林镇乌林村" in prompt
    assert "不得补成“监利市乌林镇乌林村”" in prompt
    assert "行政归属交给门店工具解析" in prompt


def test_parallel_reply_prompt_keeps_store_delivery_fact_based() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "门店必须属于客户当前可见范围" in prompt
    assert "事实不足时只问一个会实质改变回答的最小问题" in prompt
    assert "应视为当前门店匹配请求" not in prompt


def test_parallel_reply_prompt_separates_activity_from_deposit() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "预约金是一项独立成交动作" in prompt
    assert "活动介绍与预约金不得在客户第一次了解活动或价格时绑定发送" in prompt
    assert "Gate 候选是可选证据与素材，不是模板" in prompt
    assert "采用 Gate 候选时" in prompt


def test_parallel_reply_requires_a_prior_offer_and_current_action_signal_for_payment() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "当前消息之前已经真实介绍过本次活动与价格" in prompt
    assert "地址、效果、卡点排疑中至少另一把销售钥匙" in prompt
    assert "当前轮存在明确行动信号" in prompt
    assert "action=registration` 只用于权威已付后的资料登记" in prompt
    assert "订单不是发卡前置" in prompt


def test_parallel_reply_prompt_forbids_placeholder_commit_actions() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "`commit_actions` 只允许在输入明确提供权威已付" in prompt
    assert "不得在客户回复中声称后台写入已经成功" in prompt


def test_parallel_reply_prompt_requires_visible_reply_when_parallel_inputs_are_empty() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "`reply_messages` 必须非空" in prompt
    assert "不采用就不要声明采用" in prompt


def test_parallel_reply_prompt_keeps_paid_registration_fact_without_slot_script() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "只有权威支付事实才算已付" in prompt
    assert "已付" in prompt and "不得发送预约金卡" in prompt
    assert "资料登记" in prompt
    assert "已付登记每轮只补一个字段组" not in prompt


def test_parallel_reply_prompt_requires_full_deposit_explanation_before_unpaid_registration() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "未付客户要求“登记、预约、留名额、报名”" in prompt
    assert "不要只索要姓名和手机号" in prompt
    assert "每位10元预约金" in prompt
    assert "到店抵扣10元" in prompt
    assert "做的话再付258元" in prompt
    assert "未做或不满意可退" in prompt
    assert "若更早活动介绍、另一把销售钥匙和当前行动信号已满足预约金条件" in prompt


def test_parallel_prompts_keep_paid_decision_in_reply_not_gate() -> None:
    assert "不决定销售动作" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "只有权威支付事实才算已付" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "已付" in PARALLEL_REPLY_SYSTEM_PROMPT and "不得发送预约金卡" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_parallel_reply_prompt_allows_in_store_assessment_without_promising_operation() -> None:
    rules = parallel_reply_business_rules_for_model()
    assert "面对面皮肤检测和评估" in rules["AUTHORITATIVE FACTS"]["health_risk_policy"]["in_store_assessment"]


def test_parallel_gate_uses_history_only_to_rank_optional_assets() -> None:
    prompt = PARALLEL_CONTENT_GATE_SYSTEM_PROMPT

    assert "完整带时间聊天" in prompt
    assert "delivery_status=completed" in prompt
    assert "最终是否采用和重复交付由 Reply 判断" in prompt
    assert "不判断客户类型、心理、意向等级、成交阶段、固定主线或下一步" in prompt
    assert "不把“系统尚未收集某字段”当成资产相关性" in prompt
    assert "已完成资产默认作为历史事实，不机械重发" in prompt
    assert "依赖未满足时不能提名" in prompt
    assert "客户当前问题和仍然真实存在的紧邻问题" in prompt
    assert "最多一个 `direct`" in prompt
    assert "客户答“番禺区”" not in prompt


def test_parallel_gate_filters_channel_incompatible_asset_but_reply_owns_action() -> None:
    assert "人工转账" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能与小程序收款卡并存" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "payment_assessment" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "manual_transfer" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "unverified_paid_claim" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "与当前付款通道结构不兼容" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "这是结构兼容性过滤，不是替 Reply 决定成交动作" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "包含结构卡片的资产若与当前权威支付方式冲突，不得提名" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_parallel_selected_gate_candidate_requires_its_structured_material() -> None:
    state = {
        **_parallel_state("活动怎么参加"),
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "messages": [
                        {"type": "text", "content": "活动介绍"},
                        {"type": "image", "content": "https://example.invalid/activity.jpg"},
                    ],
                }
            ]
        },
        "reply_selected_content_ids": ["s10_activity_intro"],
        "reply_used_fact_refs": ["content_asset:s10_activity_intro"],
    }

    with pytest.raises(ValueError, match="selected_content_delivery_missing"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": "活动介绍"}],
            state,
        )

    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": "活动介绍"},
            {"type": "image", "order": 2, "content": "https://example.invalid/activity.jpg"},
        ],
        state,
    )


def test_parallel_selected_content_delivery_reports_all_missing_assets_at_once() -> None:
    state = {
        **_parallel_state("活动怎么参加"),
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "messages": [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
                },
                {
                    "content_id": "s10_need_and_case",
                    "messages": [{"type": "image", "content": "https://example.invalid/case.jpg"}],
                },
            ]
        },
        "reply_selected_content_ids": ["s10_activity_intro", "s10_need_and_case"],
        "reply_used_fact_refs": [
            "content_asset:s10_activity_intro",
            "content_asset:s10_need_and_case",
        ],
    }

    with pytest.raises(ValueError) as exc_info:
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": "先给您讲活动。"}],
            state,
        )

    detail = str(exc_info.value)
    assert "content_id=s10_activity_intro" in detail
    assert "content_id=s10_need_and_case" in detail


def test_parallel_selected_gate_candidate_is_not_hydrated_by_code() -> None:
    state = {
        **_parallel_state("活动怎么参加"),
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "messages": [
                        {"type": "text", "content": "活动介绍"},
                        {"type": "image", "content": {"url": "https://example.invalid/activity.jpg"}},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                }
            ]
        },
        "reply_selected_content_ids": ["s10_activity_intro"],
        "reply_used_fact_refs": ["content_asset:s10_activity_intro"],
        "reply_action": "none",
    }

    messages = [{"type": "text", "order": 1, "content": "活动内容给您说明一下。"}]

    with pytest.raises(ValueError, match="selected_content_delivery_missing"):
        validate_reply_consistency(messages, state)


def test_parallel_selected_content_repair_hint_is_structural_not_sales_decision() -> None:
    hint = _reply_repair_hint("selected_content_delivery_missing:content_id=s10_activity_intro")

    assert "补齐真实" in hint
    assert "如果候选与当前付款方式" in hint
    assert "代码不会替你自动追加" in hint


def test_parallel_reply_uses_final_targeted_repair_before_neutral_fallback() -> None:
    class _ModelClient:
        available = True
        last_usage = None

        def __init__(self) -> None:
            self.calls = 0

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            self.calls += 1
            content = "系统状态显示正在处理。" if self.calls == 1 else "好的，信息已收到。"
            return {
                "reply_messages": [{"type": "text", "order": 1, "content": content}],
                "used_fact_refs": [],
                "selected_content_ids": [],
                "action": "none",
                "action_reason": "自然承接",
                "safety_assessment": {"status": "none", "evidence_refs": []},
                "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
                "commit_actions": [],
            }

    model_client = _ModelClient()
    node = create_synthesize_reply_node(
        trace_logger=_TraceLogger(),
        model_client=model_client,
        debug_message_contents=debug_message_contents,
        reply_messages_for_model=lambda _state: [
            {"role": "system", "content": "output json"},
            {"role": "user", "content": "{}"},
        ],
        should_use_model_reply=lambda _state: True,
        validated_model_messages=validated_model_messages,
    )
    state = {
        **_parallel_state("可以先把活动留着"),
        "request_id": "parallel-final-targeted-repair",
        "trace": [],
        "errors": [],
        "warnings": [],
        "required_tools": [],
        "fact_envelope": {},
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "content_candidates": [],
        },
    }

    output = asyncio.run(node(state))

    assert model_client.calls == 2
    assert output["reply_source"] == "single_targeted_repair_model"
    assert output["fallback_source"] == ""
    assert output["reply_messages"] == [{"type": "text", "order": 1, "content": "好的，信息已收到。"}]
    assert output["selected_content_ids"] == []
    assert len(output["recovery_attempts"]) == 1
    assert output["recovery_attempts"][0]["type"] == "repair"
    assert output["recovery_attempts"][0]["succeeded"] is True


def test_parallel_reply_fallback_trace_keeps_failed_raw_json_for_audit() -> None:
    class _ModelClient:
        available = True
        last_usage = None

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {"reply_messages": []}

    node = create_synthesize_reply_node(
        trace_logger=_TraceLogger(),
        model_client=_ModelClient(),
        debug_message_contents=debug_message_contents,
        reply_messages_for_model=lambda _state: [
            {"role": "system", "content": "output json"},
            {"role": "user", "content": "{}"},
        ],
        should_use_model_reply=lambda _state: True,
        validated_model_messages=validated_model_messages,
    )
    state = {
        **_parallel_state("活动怎么参加"),
        "request_id": "parallel-failed-raw-json-audit",
        "trace": [],
        "errors": [],
        "warnings": [],
        "required_tools": [],
        "fact_envelope": {},
        "evidence_join": {"schema_version": "reply_chain_evidence_join_v1", "content_candidates": []},
    }

    output = asyncio.run(node(state))

    assert output["reply_source"] == "deterministic_neutral_final_fallback"
    model_call = state["trace"][-1]["tool_calls"][0]
    assert model_call["raw_json_output"] == {"reply_messages": []}
    assert model_call["retry"]["raw_json_output"] == {"reply_messages": []}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("invalid_reply_deposit_supporting_key", "address、effect、objection"),
        ("payment_collection_requires_prior_activity_evidence", "offer_prior_turn_refs"),
        ("payment_collection_requires_prior_supporting_key_evidence", "supporting_refs"),
        ("payment_collection_requires_current_action_signal_evidence", "current_intent_refs"),
        ("selected_content_id_not_in_gate_candidates:s10_store_prompt", "allowed_selected_content_ids"),
        ("Model reply_messages are empty", "至少一条客户可见 text"),
    ],
)
def test_parallel_reply_repair_hints_are_specific(error: str, expected: str) -> None:
    assert expected in _reply_repair_hint(error)


def test_parallel_reply_repair_includes_previous_json_output() -> None:
    from app.graph.nodes.reply_nodes import _reply_retry_messages

    messages = _reply_retry_messages(
        [{"role": "system", "content": "output json"}],
        ValueError("payment_action_requires_payment_collection"),
        previous_payload={"reply_messages": ["先付10元预约金。"], "action": "payment"},
    )

    assert messages[-2]["role"] == "assistant"
    assert '"action":"payment"' in messages[-2]["content"]
    assert "repair 必须保留 payment 并补齐卡片" in messages[-1]["content"]


def test_parallel_reply_repairs_partial_selected_candidate_instead_of_silently_mutating_it() -> None:
    image_url = "https://example.invalid/deposit.jpg"

    class _ModelClient:
        available = True
        last_usage = None

        def __init__(self) -> None:
            self.calls = 0

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            self.calls += 1
            selected = ["s10_deposit_close"] if self.calls == 1 else []
            return {
                "reply_messages": [{"type": "text", "order": 1, "content": "活动规则给您说明清楚了。"}],
                "used_fact_refs": [],
                "selected_content_ids": selected,
                "action": "offer",
                "action_reason": "只参考候选文字",
                "safety_assessment": {"status": "none", "evidence_refs": []},
                "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
                "commit_actions": [],
            }

    model_client = _ModelClient()
    node = create_synthesize_reply_node(
        trace_logger=_TraceLogger(),
        model_client=model_client,
        debug_message_contents=debug_message_contents,
        reply_messages_for_model=lambda _state: [
            {"role": "system", "content": "output json"},
            {"role": "user", "content": "{}"},
        ],
        should_use_model_reply=lambda _state: True,
        validated_model_messages=validated_model_messages,
    )
    state = {
        **_parallel_state("活动规则是什么"),
        "request_id": "parallel-partial-candidate-metadata",
        "trace": [],
        "errors": [],
        "warnings": [],
        "required_tools": [],
        "fact_envelope": {},
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "content_candidates": [
                {
                    "content_id": "s10_deposit_close",
                    "messages": [
                        {"type": "text", "content": "活动规则给您说明清楚了。"},
                        {"type": "image", "content": image_url},
                    ],
                }
            ],
        },
    }

    output = asyncio.run(node(state))

    assert model_client.calls == 2
    assert output["reply_source"] == "single_targeted_repair_model"
    assert output["reply_messages"] == [{"type": "text", "order": 1, "content": "活动规则给您说明清楚了。"}]
    assert output["selected_content_ids"] == []
    assert not any(item.get("message") == "partial_gate_candidate_not_committed" for item in output["warnings"])


def test_parallel_reply_recovery_keeps_complete_gate_candidate_evidence() -> None:
    activity_text = "周年庆活动总价268元，线上每位10元预约金，到店抵扣，未做或不满意可退。"
    state = {
        **_parallel_state("这个活动怎么参加"),
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "messages": [
                        {"type": "text", "order": 1, "content": activity_text},
                        {
                            "type": "payment_collection",
                            "order": 2,
                            "content": {"amount": 10},
                        },
                    ],
                }
            ],
        },
    }

    messages = _reply_recovery_messages(state, primary_error=ValueError("schema repair"))

    assert activity_text in messages[1]["content"]
    assert '"content_id":"s10_activity_intro"' in messages[1]["content"]


def test_registration_confirmation_fact_required_has_specific_repair_hint() -> None:
    hint = _reply_repair_hint("registration_confirmation_fact_required")

    assert "不能说已经登记" in hint
    assert "action=payment" in hint
    assert "payment_collection" in hint


def test_registration_action_without_paid_context_repair_covers_full_deposit_asset() -> None:
    hint = _reply_repair_hint("registration_action_requires_paid_context")

    assert "registration 只用于已付后" in hint
    assert "action=payment" in hint
    assert "image 和 payment_collection" in hint
    assert "deposit_evidence" in hint


def test_payment_action_repair_cannot_evade_missing_card_by_cancelling_payment() -> None:
    hint = _reply_repair_hint("payment_action_requires_payment_collection")

    assert "repair 必须保留 payment 并补齐卡片" in hint
    assert "不能改成 none/ask/offer" in hint


def test_tool_planner_repairs_invalid_tool_schema_once() -> None:
    class _ModelClient:
        available = True
        last_usage = None

        def __init__(self) -> None:
            self.calls = 0

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            self.calls += 1
            if self.calls == 1:
                return {
                    "tool_calls": [
                        {
                            "name": "kb_search",
                            "arguments": {"kb_name": "case_studies"},
                            "evidence_refs": ["current_message"],
                        }
                    ]
                }
            return {
                "tool_calls": [
                    {
                        "name": "kb_search",
                        "arguments": {"kb_name": "case_studies", "query": "效果怎么样，有图吗"},
                        "evidence_refs": ["current_message"],
                    }
                ],
                "reason": "补齐只读查询参数",
            }

    model_client = _ModelClient()
    result = asyncio.run(
        _run_tool_planner(
            {
                "shared_context": {
                    "conversation": [
                        {
                            "message_ref": "current_message",
                            "role": "customer",
                            "content": "效果怎么样，有图吗",
                        }
                    ]
                }
            },
            model_client,
        )
    )

    assert model_client.calls == 2
    assert result["status"] == "completed"
    assert result["repair_attempted"] is True
    assert result["initial_violations"] == ["tool_call_missing_argument:kb_search:query"]
    assert result["violations"] == []
    assert result["tool_calls"][0]["query"] == "效果怎么样，有图吗"


def test_tool_planner_always_applies_authoritative_location_card_arguments() -> None:
    class _ModelClient:
        available = True
        last_usage = None

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "tool_calls": [
                    {
                        "name": "customer_store_lookup",
                        "arguments": {"query": "萤火虫大厦"},
                        "evidence_refs": ["current_message"],
                    },
                    {
                        "name": "distance_calculate",
                        "arguments": {
                            "origin": "model-shortened-origin",
                            "candidate_source": "customer_store_lookup",
                        },
                        "evidence_refs": ["current_message"],
                    },
                ]
            }

    result = asyncio.run(
        _run_tool_planner(
            {
                "shared_context": {
                    "current_message": {
                        "message_ref": "current_message",
                        "message_type": "location",
                    },
                    "authoritative_facts": {
                        "location_card": {
                            "coordinates": "24.535414,118.152077",
                            "title": "萤火虫大厦",
                            "address": "福建省厦门市湖里区岐山北二路1000号",
                        }
                    },
                }
            },
            _ModelClient(),
        )
    )

    assert result["status"] == "completed"
    assert result["protocol_recovery"] is False
    assert result["tool_calls"] == _protocol_required_read_only_tools(
        {
            "shared_context": {
                "current_message": {"message_type": "location"},
                "authoritative_facts": {
                    "location_card": {
                        "coordinates": "24.535414,118.152077",
                        "title": "萤火虫大厦",
                        "address": "福建省厦门市湖里区岐山北二路1000号",
                    }
                },
            }
        }
    )


def test_visible_store_scope_is_authoritative_for_store_card_and_address_text() -> None:
    store = {
        "store_id": "301",
        "store_name": "广州番禺店",
        "province": "广东省",
        "city": "广州市",
        "district": "番禺区",
        "store_address": "广东省广州市番禺区市桥街兴泰路",
    }
    state = {
        "normalized_content": "番禺区",
        "customer_store_knowledge": {"stores": [store]},
        "store_scope_summary": {
            "relevant_regions": [
                {
                    "city": "广州市",
                    "requested_areas": ["番禺区"],
                    "requested_district_stores": [store],
                    "stores": [store],
                }
            ]
        },
        "fact_envelope": {"structured_facts": {}},
    }
    messages = [
        {"type": "text", "order": 1, "content": "番禺区这边是广州番禺店，地址在市桥街兴泰路。"},
        {"type": "store_address", "order": 2, "content": {"store_id": "301"}},
    ]

    validate_reply_consistency(messages, state)


def test_full_conversation_assigns_stable_message_refs() -> None:
    conversation = _conversation(
        {
            "conversation_turns": [
                {"role": "customer", "content": "时间还不确定", "sent_at": "2026-08-08T10:00:00+08:00"},
                {"role": "assistant", "content": "好的，时间后面定。", "sent_at": "2026-08-08T10:00:05+08:00"},
            ],
            "normalized_content": "我已经说了时间不确定",
        }
    )

    assert [item["message_ref"] for item in conversation] == ["history_1", "history_2"]
    assert conversation[0]["sent_at"] == "2026-08-08T10:00:00+08:00"


def test_full_conversation_removes_platform_copy_of_current_message() -> None:
    conversation = _conversation(
        {
            "conversation_turns": [
                {"role": "assistant", "content": "您在哪个城市或区？"},
                {"role": "customer", "content": "双流区"},
            ],
            "normalized_content": "双流区",
        }
    )

    assert conversation == [
        {"role": "assistant", "content": "您在哪个城市或区？", "message_ref": "history_1"}
    ]


def test_gate_uses_same_full_timestamped_conversation_as_reply() -> None:
    state = {
        "conversation_history": ["小贝: 旧的请求摘要"],
        "shared_context": {
            "conversation": [
                {
                    "message_ref": "history_1",
                    "role": "customer",
                    "content": "时间还不确定",
                    "sent_at": "2026-08-08T10:00:00+08:00",
                },
                {
                    "message_ref": "history_2",
                    "role": "assistant",
                    "content": "好的，后面定。",
                    "sent_at": "2026-08-08T10:00:05+08:00",
                },
                {"message_ref": "current_message", "role": "customer", "content": "我已经说了好几遍"},
            ]
        },
    }

    assert _gate_conversation_history(state) == [
        "[2026-08-08T10:00:00+08:00] 用户: 时间还不确定",
        "[2026-08-08T10:00:05+08:00] 小贝: 好的，后面定。",
    ]


def test_shared_order_facts_drop_old_memory_current_order_label() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {
                "source": "platform_agent",
                "orders": [{"order_id": "order-1", "is_current_order": True, "prepay_paid": False}],
                "appointment": {"appointment_id": "appt-1"},
            }
        }
    )

    assert facts["orders"] == [{"order_id": "order-1", "prepay_paid": False}]
    assert facts["appointment"] == {"appointment_id": "appt-1"}


def test_memory_fallback_appointment_is_not_exposed_as_authoritative() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {
                "source": "local_memory_fallback",
                "appointment": {"appointment_id": "old-memory-appt"},
            }
        }
    )

    assert "appointment" not in facts


def test_parallel_payment_does_not_reuse_unscoped_memory_paid_state() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {"source": "local_memory_fallback", "orders": []},
            "payment_state": "paid_by_screenshot",
            "payment_source": "customer_memory",
        }
    )

    assert "resolved_payment" not in facts


def test_parallel_payment_keeps_current_platform_transfer_event() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {"source": "platform_agent", "orders": []},
            "payment_state": "paid_by_platform_transfer_event",
            "payment_source": "platform.unknown_message_transfer",
        }
    )

    assert facts["resolved_payment"]["deposit_state"] == "paid_by_platform_transfer_event"


def test_commit_actions_require_paid_and_complete_registration_facts() -> None:
    base_scope = {
        "persistence_allowed": True,
        "wechat": "sim_wechat",
        "sales_contact_key": "sales_contact:sim",
    }
    unpaid_state = {
        "memory_persist_allowed": True,
        "wechat": "sim_wechat",
        "shared_context": {
            "customer_scope": base_scope,
            "current_message": {"content": "张三 13800138000"},
            "conversation": [],
            "authoritative_facts": {
                "orders_and_payment": {},
                "raw_visible_store_records": [{"store_id": "10"}],
                "request_store_facts": {"confirmed_store_id": "10"},
            },
        },
    }
    violations = _commit_action_violations(
        "create_work_order",
        {"customer_name": "张三", "mobile": "13800138000", "store_id": "10"},
        unpaid_state,
        evidence_refs=["current_message", "request_store:10"],
    )
    assert "commit_action_requires_paid_deposit:create_work_order" in violations
    assert "commit_action_requires_paid_evidence_ref:create_work_order" not in violations

    paid_state = {
        "memory_persist_allowed": True,
        "wechat": "sim_wechat",
        "shared_context": {
            "customer_scope": base_scope,
            "current_message": {"content": "13800138000"},
            "conversation": [],
            "authoritative_facts": {
                "orders_and_payment": {
                    "resolved_payment": {"deposit_state": "paid_by_platform_transfer_event"}
                },
                "raw_visible_store_records": [{"store_id": "10"}],
                "request_store_facts": {"confirmed_store_id": "10"},
            }
        }
    }
    violations = _commit_action_violations(
        "create_work_order",
        {"customer_name": "", "mobile": "13800138000", "store_id": "10"},
        paid_state,
        evidence_refs=["current_message", "request_store:10", "payment_fact:authoritative_paid"],
    )
    assert "commit_action_missing_customer_name:create_work_order" in violations


def test_commit_action_accepts_only_sourced_registration_and_visible_store_anchor() -> None:
    state = {
        "memory_persist_allowed": True,
        "wechat": "sim_wechat",
        "shared_context": {
            "customer_scope": {
                "persistence_allowed": True,
                "wechat": "sim_wechat",
                "sales_contact_key": "sales_contact:sim",
            },
            "current_message": {"content": "张三 13800138000"},
            "conversation": [],
            "authoritative_facts": {
                "orders_and_payment": {
                    "resolved_payment": {"deposit_state": "paid_by_platform_transfer_event"}
                },
                "raw_visible_store_records": [{"store_id": "10"}],
                "request_store_facts": {"confirmed_store_id": "10"},
            },
        },
    }

    assert _commit_action_violations(
        "create_work_order",
        {"customer_name": "张三", "mobile": "13800138000", "store_id": "10"},
        state,
        evidence_refs=[
            "current_message",
            "request_store:10",
            "payment_fact:authoritative_paid",
        ],
    ) == []

    violations = _commit_action_violations(
        "create_work_order",
        {"customer_name": "张三", "mobile": "13800138000", "store_id": "99"},
        state,
        evidence_refs=[
            "current_message",
            "request_store:10",
            "payment_fact:authoritative_paid",
        ],
    )
    assert "commit_action_store_not_customer_visible:create_work_order" in violations
    assert "commit_action_store_missing_anchor:create_work_order" in violations


def test_gate_and_tool_planner_execute_concurrently(monkeypatch) -> None:
    async def fake_gate(state, service):
        await asyncio.sleep(0.08)
        return {"duration_ms": 80, "content_candidate_ids": [], "content_candidates": []}

    async def fake_planner(state, model_client):
        await asyncio.sleep(0.08)
        return {"duration_ms": 80, "tool_calls": [], "missing_facts": []}

    monkeypatch.setattr(parallel_reply_chain, "_run_content_gate", fake_gate)
    monkeypatch.setattr(parallel_reply_chain, "_run_tool_planner", fake_planner)
    node = create_parallel_evidence_node(
        trace_logger=_TraceLogger(),
        model_client=object(),
        sop_execution_service=object(),
    )

    started = time.perf_counter()
    result = asyncio.run(node({"shared_context": {}, "trace": []}))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.14
    assert result["parallel_branch_metrics"]["parallel_expected_elapsed_ms"] == 80


def test_one_parallel_branch_failure_preserves_the_other_branch(monkeypatch) -> None:
    async def failed_gate(state, service):
        del state, service
        raise TimeoutError("gate timed out")

    async def successful_planner(state, model_client):
        del state, model_client
        return {
            "schema_version": "tool_plan_v1",
            "status": "ok",
            "duration_ms": 12,
            "tool_calls": [
                {
                    "name": "kb_search",
                    "query": "真实效果案例",
                    "kb_name": "case_studies",
                    "evidence_refs": ["current_message"],
                }
            ],
            "missing_facts": [],
        }

    monkeypatch.setattr(parallel_reply_chain, "_run_content_gate", failed_gate)
    monkeypatch.setattr(parallel_reply_chain, "_run_tool_planner", successful_planner)
    node = create_parallel_evidence_node(
        trace_logger=_TraceLogger(),
        model_client=object(),
        sop_execution_service=object(),
    )

    result = asyncio.run(node({"shared_context": {}, "trace": []}))

    assert result["content_gate_result"]["status"] == "error"
    assert "gate timed out" in result["content_gate_result"]["error"]
    assert result["tool_plan"]["status"] == "ok"
    assert result["tool_plan"]["tool_calls"][0]["name"] == "kb_search"


def test_shared_context_contains_scoped_sop_progress_before_parallel_branches() -> None:
    class _SopService:
        def reply_chain_content_catalog(self):
            return {"schema_version": "reply_chain_content_index_v2", "sop_packs": []}

        def reply_chain_sop_progress(self, request, *, request_context):
            assert request.customer_id == "sim_customer"
            assert request_context["wechat"] == "sim_wechat"
            return {
                "status": "available",
                "source": "scoped_sop_send_records",
                "completed_pack_ids": ["s10_new_customer_opening"],
                "completed_categories": ["opening"],
                "unfinished_sops": [{"id": "s10_need_and_case"}],
            }

    node = create_shared_context_node(
        trace_logger=_TraceLogger(),
        sop_execution_service=_SopService(),
    )
    result = asyncio.run(
        node(
            {
                "content": "效果怎么样",
                "normalized_content": "效果怎么样",
                "customer_id": "sim_customer",
                "wechat": "sim_wechat",
                "request_context": {"wechat": "sim_wechat"},
                "trace": [],
            }
        )
    )

    progress = result["shared_context"]["authoritative_facts"]["sop_progress"]
    assert progress["status"] == "available"
    assert progress["completed_pack_ids"] == ["s10_new_customer_opening"]
    assert progress["unfinished_sops"] == [{"id": "s10_need_and_case"}]


def test_parallel_reply_does_not_use_legacy_semantic_model_tier() -> None:
    state = {
        "evidence_join": {"schema_version": "reply_chain_evidence_join_v1"},
        "customer_type": "complaint",
        "main_blocker": "refund",
        "handoff": {"needed": True},
    }

    assert _needs_strong_reply_model(state) is False


def test_parallel_reply_does_not_manufacture_payment_card() -> None:
    state = {
        **_parallel_state("怎么付费"),
        "payment_decision": {"action": "send_now"},
    }

    assert (
        _maybe_build_required_payment_collection_fallback(
            state,
            ValueError("payment_collection_required_when_reply_promises_payment_entry"),
            messages=[{"type": "text", "content": "我把入口发您。"}],
        )
        is None
    )


def test_gate_prompt_declares_parallel_candidate_only_boundary() -> None:
    assert "reply_chain_mode=parallel_candidate_only" in SOP_CHAT_GATE_SYSTEM_PROMPT
    assert "不决定客户心理、成交阶段" in SOP_CHAT_GATE_SYSTEM_PROMPT
    assert "资产是事实、证据和结构素材的组合，不是成品回复模板" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "不要按词语命中或配置顺序补流程" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "activity_offer" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "deposit_close" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "# 候选校准" not in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "客户第一次问" not in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_gate_keeps_multiple_model_nominated_content_candidates() -> None:
    class _GateService:
        shared_state = None

        async def evaluate_chat_gate(self, request, **kwargs):
            del request
            self.shared_state = kwargs.get("shared_state")
            return {
                "mode": "ai_then_sop",
                "route": "ai_then_sop",
                "coverage": "partial",
                "selected_pack_ids": ["s10_need_and_case", "s10_activity_intro"],
                "candidate_packs": [
                    {
                        "content_id": "s10_need_and_case",
                        "content_type": "sop",
                        "name": "需求与效果承接",
                        "messages": [{"type": "image", "content": {"url": "https://example.test/case.jpg"}}],
                    },
                    {
                        "content_id": "s10_activity_intro",
                        "content_type": "sop",
                        "name": "活动介绍",
                        "messages": [{"type": "text", "content": {"text": "活动事实"}}],
                    },
                ],
            }

    service = _GateService()
    shared = {
        "schema_version": "shared_context_v2",
        "conversation": [{"message_ref": "conv_001", "role": "customer", "content": "历史原话"}],
    }
    result = asyncio.run(
        parallel_reply_chain._run_content_gate(
            {"shared_context": shared, "conversion_stage": "legacy_stage", "customer_type": "legacy_type"},
            service,
        )
    )

    assert result["content_candidate_ids"] == ["s10_need_and_case", "s10_activity_intro"]
    assert [item["content_id"] for item in result["content_candidates"]] == [
        "s10_need_and_case",
        "s10_activity_intro",
    ]
    assert result["candidate_commit"]["sop_pack_ids"] == [
        "s10_need_and_case",
        "s10_activity_intro",
    ]
    assert "selected_sop_ids" not in result
    assert "direct_reply_eligible" not in result
    assert "legacy_gate_mode" not in result
    assert service.shared_state == {"shared_context": shared}


def test_parallel_gate_ignores_legacy_direct_reply_payload() -> None:
    class _LegacyGateService:
        async def evaluate_chat_gate(self, request, **kwargs):
            del request, kwargs
            return {
                "mode": "sop_only",
                "reply_messages": [{"type": "text", "content": "legacy direct reply"}],
                "candidate_packs": [],
            }

    result = asyncio.run(
        parallel_reply_chain._run_content_gate(
            {"shared_context": {"schema_version": "shared_context_v2"}},
            _LegacyGateService(),
        )
    )

    assert result["route_advice"] == "tools_only"
    assert result["content_candidates"] == []
    assert "reply_messages" not in result


def test_parallel_repair_context_keeps_neutral_ref_role_content_options() -> None:
    state = {
        **_parallel_state("那怎么报名"),
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "shared_context": {
                "schema_version": "shared_context_v2",
                "conversation": [
                    {
                        "message_ref": "conv_001",
                        "role": "assistant",
                        "content": "我先给您看效果参考。",
                    },
                    {
                        "message_ref": "conv_002",
                        "role": "customer",
                        "content": "我这个斑有五六年了。",
                    },
                    {
                        "message_ref": "current_message",
                        "role": "customer",
                        "content": "那怎么报名",
                    },
                ],
            },
            "content_candidates": [],
        },
    }

    context = _parallel_reply_repair_context(state)

    assert context["prior_customer_message_refs"] == ["conv_002"]
    assert context["prior_assistant_message_refs"] == ["conv_001"]
    assert context["prior_message_options"] == [
        {"ref": "conv_001", "role": "assistant", "content": "我先给您看效果参考。"},
        {"ref": "conv_002", "role": "customer", "content": "我这个斑有五六年了。"},
    ]


def test_parallel_repair_hint_combines_asset_delivery_and_deposit_provenance() -> None:
    hint = _reply_repair_hint(
        "parallel_reply_hard_violations::"
        "selected_content_delivery_missing:content_id=s10_deposit_close;required=image:x,payment_collection:10;;"
        "payment_collection_requires_customer_engaged_supporting_key_evidence"
    )

    assert "组合修复" in hint
    assert "prior_message_options" in hint
    assert "content_candidate_delivery_requirements" in hint
    assert "不要只修第一项" in hint


def test_tool_planner_requires_current_turn_store_lookup_even_when_scope_is_visible() -> None:
    assert "即使 `visible_store_scope` 已列出 1–3 家" in parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT
    assert "权限列表本身不能替代本轮匹配" in parallel_reply_chain.TOOL_PLANNER_SYSTEM_PROMPT


def test_deterministic_join_does_not_expose_legacy_fact_envelope() -> None:
    node = create_evidence_join_node(trace_logger=_TraceLogger())
    result = asyncio.run(
        node(
            {
                "shared_context": {"schema_version": "shared_context_v2"},
                "content_gate_result": {"content_candidates": []},
                "tool_plan": {"tool_calls": [], "missing_facts": []},
                "tool_results": {"case_studies": {"documents": []}},
                "fact_envelope": {
                    "structured_facts": {
                        "store_resolution_fact": {
                            "status": "send_single",
                            "delivery_store_ids": ["601"],
                        }
                    },
                    "customer_type": "high_intent",
                    "next_step": "push_payment",
                },
                "trace": [],
            }
        )
    )

    joined = result["evidence_join"]
    assert "fact_envelope" not in joined
    assert joined["normalized_tool_facts"] == {
        "structured_facts": {
            "store_resolution_fact": {
                "status": "send_single",
                "delivery_store_ids": ["601"],
            }
        }
    }
    assert "customer_type" not in str(joined)
    assert "next_step" not in str(joined)


def test_reply_graph_bundle_has_no_legacy_planner_or_finalize_graph() -> None:
    assert set(ReplyGraphs.__dataclass_fields__) == {"full_graph", "commit_graph"}


def test_parallel_public_route_does_not_publish_legacy_sales_semantics() -> None:
    route = planner_public_route(
        {
            "evidence_join": {"schema_version": "reply_chain_evidence_join_v1"},
            "planner_stage": "S3",
            "customer_type": "high_intent",
            "main_blocker": "price",
            "next_step": "push_payment",
        }
    )

    assert route["scene"] == "parallel_reply"
    assert route["subflow"] == "reply"
    assert route["customer_type"] == ""
    assert route["main_blocker"] == ""
    assert route["next_step"] == ""


def test_shared_context_reuses_only_authoritative_stored_payment_fact() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {"orders": []},
            "customer_basic_info": {
                "deposit_state": {
                    "status": "paid_by_platform_transfer_event",
                    "source": "platform.unknown_message_transfer",
                    "amount": 10,
                }
            },
        }
    )

    assert facts["resolved_payment"]["deposit_state"] == "paid_by_platform_transfer_event"
    assert facts["resolved_payment"]["source"] == "platform.unknown_message_transfer"


def test_shared_context_rejects_untrusted_stored_payment_claim() -> None:
    facts = _authoritative_order_payment_facts(
        {
            "customer_context": {"orders": []},
            "customer_basic_info": {
                "deposit_state": {
                    "status": "paid_by_screenshot",
                    "source": "customer_text_claim",
                }
            },
        }
    )

    assert "resolved_payment" not in facts
