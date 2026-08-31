from __future__ import annotations

from app.services.run_observability import (
    build_v3_run_observability,
    enrich_v3_run_observability,
)


def _v3_state() -> dict:
    return {
        "content": "这家店离我太远了",
        "conversation_history": ["客户：武汉有门店吗", "小贝：已经给您发了门店"],
        "request_context": {
            "interface_version": "v3",
            "reply_chain_mode": "model_led_sales_brain_v3",
            "msgtype": "text",
        },
        "shared_context": {
            "conversation": [
                {
                    "message_ref": "message:12",
                    "role": "customer",
                    "content": "这家店离我太远了",
                }
            ]
        },
        "semantic_route": {
            "status": "ok",
            "phase": "post_store_final",
            "classification_status": "clear",
            "checkpoint": {
                "primary_code": "distance",
                "primary_type_id": 12,
                "primary_type_name": "距离/便利",
                "primary_tag_id": 36,
                "primary_tag_name": "太远了不方便过来",
                "secondary_code": "",
                "evidence_refs": ["message:12"],
                "reason": "客户明确表示当前推荐门店太远",
            },
            "sequence_match": {
                "sequence_ids": ["18"],
                "alternative_sequence_ids": [],
                "relevant_step_ids": ["181"],
                "excluded_sequence_ids": [],
                "reason": "当前应先承接距离顾虑，再转向真实效果价值",
            },
            "store_query": {
                "required": True,
                "purpose": "distance_compare",
                "destination_hint": "武汉汉口",
            },
        },
        "sales_recall": {
            "status": "ok",
            "candidate_count": 1,
            "sequence_candidates": [
                {
                    "sequence_id": "18",
                    "sequence_name": "距离卡点快速推进",
                    "checkpoint_code": "distance",
                    "checkpoint_name": "距离/便利",
                    "selection_reason": "换价值维度",
                    "steps": [
                        {
                            "step_id": "181",
                            "sort_order": 1,
                            "action_code": "empathy",
                            "action_name": "共情引导",
                        }
                    ],
                }
            ],
            "script_query_results": [{"status": "ok", "total": 1}],
            "candidates": [
                {
                    "script_id": "172",
                    "source_id": "D27",
                    "script_name": "距离卡点-价值承接",
                    "checkpoint_type": {"id": 12, "name": "距离/便利"},
                    "checkpoint_tag": {"id": 36, "name": "太远了不方便过来"},
                    "action_code": "empathy",
                    "action_name": "共情引导",
                    "reference_text": "不少外地客户也会专程过来。",
                    "paragraphs": [
                        {
                            "source_ref": "follow_script:172:p1",
                            "messages": [
                                {"type": "text", "content": "不少外地客户也会专程过来。"},
                                {"type": "image", "url": "https://example.com/case.png", "title": "效果案例"},
                            ],
                        }
                    ],
                }
            ],
        },
        "reply_knowledge_use": {
            "sequence_id": "18",
            "sequence_name": "距离卡点快速推进",
            "step_id": "181",
            "checkpoint_code": "distance",
            "action_code": "empathy",
            "selected_script_ids": ["D27"],
            "reason": "采用距离价值承接表达",
        },
        "selected_content_ids": ["follow_script:D27:p1"],
        "content_selection_metrics": {
            "nominated_ids": ["follow_script:D27:p1"],
            "adopted_ids": ["follow_script:D27:p1"],
            "delivered_ids": ["follow_script:D27:p1"],
        },
        "store_resolution_fact": {
            "status": "resolved",
            "candidate_search_complete": True,
            "candidate_count": 1,
            "delivery_store_ids": ["241"],
        },
        "evidence_join": {
            "normalized_tool_facts": {
                "structured_facts": {
                    "store_facts": [
                        {
                            "store_id": "241",
                            "store_name": "武汉江汉店",
                            "store_address": "武汉市江汉区测试路1号",
                            "distance_km": 12.3,
                        }
                    ]
                }
            }
        },
        "reply_messages": [
            {"type": "text", "content": "确实要跑一趟，不过这个案例您先看下，值不值得来您一眼就能判断。"},
            {"type": "image", "content": {"url": "https://example.com/case.png"}},
        ],
        "reply_action": "advance",
        "reply_action_reason": "用真实案例降低距离顾虑",
        "reply_source": "parallel_reply_model",
        "trace": [
            {"node": "v3_semantic_route_and_knowledge", "duration_ms": 1200},
            {"node": "synthesize_reply", "duration_ms": 2300},
        ],
        "errors": [],
        "warnings": [],
    }


def test_build_v3_observability_exposes_match_adoption_and_delivery() -> None:
    summary = build_v3_run_observability(_v3_state())

    assert summary["customer_input"]["content"] == "这家店离我太远了"
    assert summary["checkpoint_decision"]["primary"]["name"] == "距离/便利"
    assert summary["checkpoint_decision"]["evidence"] == [
        {"ref": "message:12", "quote": "这家店离我太远了"}
    ]
    execution = summary["knowledge_match"]["execution"]
    assert execution == {
        "router_invoked": True,
        "router_status": "ok",
        "router_phase": "post_store_final",
        "sequence_index_count": 1,
        "knowledge_status": "ok",
        "script_lookup_invoked": True,
        "script_lookup_count": 1,
        "selector_invoked": False,
    }
    sequence = summary["knowledge_match"]["matched_sequences"][0]
    assert sequence["sequence_id"] == "18"
    assert sequence["adopted"] is True
    assert sequence["steps"][0]["adopted"] is True
    script = summary["knowledge_match"]["script_candidates"][0]
    assert script["script_code"] == "D27"
    assert script["adopted"] is True
    assert script["delivered"] is True
    assert script["media"][0]["url"] == "https://example.com/case.png"
    assert summary["store_workflow"]["stores"][0]["store_id"] == "241"
    assert summary["reply_result"]["action"] == "advance"


def test_observability_does_not_infer_knowledge_use_from_reply_text() -> None:
    state = _v3_state()
    state["reply_knowledge_use"] = {}
    state["content_selection_metrics"] = {}
    summary = build_v3_run_observability(state)

    script = summary["knowledge_match"]["script_candidates"][0]
    assert script["adopted"] is False
    assert script["delivered"] is False
    assert summary["knowledge_match"]["adopted"]["script_ids"] == []


def test_observability_matches_platform_script_id_to_script_code_candidate() -> None:
    state = _v3_state()
    state["reply_knowledge_use"]["selected_script_ids"] = ["172"]

    summary = build_v3_run_observability(state)

    script = summary["knowledge_match"]["script_candidates"][0]
    assert script["script_id"] == "172"
    assert script["script_code"] == "D27"
    assert script["adopted"] is True
    assert script["delivered"] is True


def test_observability_reports_store_call_from_final_resolution_after_post_route() -> None:
    state = _v3_state()
    state["semantic_route"]["store_query"] = {
        "required": False,
        "purpose": "none",
        "destination_hint": "",
    }

    summary = build_v3_run_observability(state)

    assert summary["overview"]["store_called"] is True
    assert summary["store_workflow"]["called"] is True
    assert summary["store_workflow"]["status"] == "resolved"


def test_enrich_observability_uses_actual_async_dispatch_result() -> None:
    output = {
        "observability_v3": build_v3_run_observability(_v3_state()),
        "strategy_data_callback": {"status": "sent", "task_id": "task-1"},
    }
    enrich_v3_run_observability(
        output,
        dispatch={
            "id": "dispatch-1",
            "status": "delivered",
            "expected_count": 2,
            "succeeded_count": 2,
            "failed_count": 0,
            "reply_messages": _v3_state()["reply_messages"],
            "items": [
                {"message_index": 0, "message_type": "text", "status": "delivered"},
                {"message_index": 1, "message_type": "image", "status": "delivered"},
            ],
        },
    )

    assert output["observability_v3"]["delivery"]["dispatch_id"] == "dispatch-1"
    assert output["observability_v3"]["delivery"]["mode"] == "async_callback"
    assert output["observability_v3"]["delivery"]["callback_expected"] is True
    assert output["observability_v3"]["delivery"]["succeeded_count"] == 2
    assert output["observability_v3"]["strategy_callback"]["status"] == "sent"


def test_direct_reply_is_reported_as_sync_return_without_async_callback() -> None:
    state = _v3_state()
    state["reply_control"] = {
        "sync_return": {
            "type": "direct_reply",
            "reply_messages": state["reply_messages"],
        }
    }
    state["async_final_reply"] = {"scheduled": False, "status": "not_required"}

    summary = build_v3_run_observability(state)

    assert summary["delivery"]["mode"] == "sync_return"
    assert summary["delivery"]["status"] == "direct_response_returned"
    assert summary["delivery"]["callback_expected"] is False
    assert "避免重复发送" in summary["delivery"]["callback_reason"]


def test_non_v3_run_has_no_v3_observability_projection() -> None:
    state = _v3_state()
    state["request_context"]["interface_version"] = "v2"
    assert build_v3_run_observability(state) == {}
