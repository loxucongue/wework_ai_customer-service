from __future__ import annotations

from app.services.first_day_outreach_log import (
    build_first_day_run_business_summary,
    build_first_day_run_observability,
)
from app.services.outreach.first_day import (
    FIRST_DAY_COMPLETION_SCENES,
    _first_day_scene_analysis_error,
    _first_day_writer_payload,
)
from app.services.outreach_assets import (
    build_appointment_blocker_asset_catalog,
    build_outreach_asset_model_context,
)
from app.services.outreach_first_day_prompts import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT,
)


def _scene_analysis() -> dict[str, object]:
    completion = {
        scene: {
            "status": "partial" if scene in {"effect_proof", "activity_intro"} else "not_delivered",
            "message_indexes": [],
            "asset_ids": [],
            "summary": "尚未完成",
        }
        for scene in FIRST_DAY_COMPLETION_SCENES
    }
    return {
        "eligible": True,
        "suppress_reason": "",
        "hard_boundary": {"active": False, "type": "none", "message_indexes": [], "fact": ""},
        "precedence_decision": {
            "row_id": "no_blocker_sop_progression",
            "message_indexes": [0],
            "reason": "继续主线",
        },
        "current_scene": "effect_proof",
        "scene_completion_matrix": completion,
        "delivered_scenes": [],
        "unresolved_customer_need": "需要效果证据",
        "customer_mainline": {
            "latest_customer_main_need": "了解效果",
            "silence_barrier": "尚未看到真实素材",
            "symptom_role": "无",
            "next_business_action": "直接展示效果素材",
        },
        "step1_scene": "effect_proof",
        "step2_scene": "activity_intro",
        "step1_objective": "展示真实效果",
        "step2_objective": "说明活动价值",
        "forbidden_repetitions": [],
        "writer_context_message_indexes": [0],
        "selected_source_ids": {
            "step1": ["sop-pack:effect"],
            "step2": ["sop-pack:activity"],
        },
        "required_assets": {
            "step1": {"strategy": "none", "asset_id": "", "reason": ""},
            "step2": {
                "strategy": "configured_image",
                "asset_id": "sop-pack:activity:2",
                "reason": "活动图",
            },
        },
        "payment_action": {"step": 0, "allowed": False, "reason": "不发付款卡"},
        "confidence": 0.9,
        "message_index_base": 0,
        "evidence": [{"message_index": 0, "fact": "客户询问效果"}],
    }


def _snapshot() -> dict[str, object]:
    return {
        "recent_messages": [
            {
                "role": "user",
                "content": "这个效果怎么样",
                "created_at": "2026-09-07T08:00:00+00:00",
            }
        ],
        "conversation_activity": {"customer_silence_minutes": 3},
        "customer_relation": {"available": True, "takeover": {"mode": "ai"}},
        "asset_availability_summary": {
            "total_count": 3,
            "available_count": 2,
            "recently_sent_count": 1,
            "image_count": 3,
            "video_count": 0,
        },
        "asset_catalog": [
            {
                "asset_id": "sop-pack:effect:2",
                "source_id": "sop-pack:effect",
                "type": "image",
                "name": "效果图一",
                "available_to_send": True,
            },
            {
                "asset_id": "sop-pack:effect:3",
                "source_id": "sop-pack:effect",
                "type": "image",
                "name": "效果图二",
                "available_to_send": True,
            },
            {
                "asset_id": "sop-pack:activity:2",
                "source_id": "sop-pack:activity",
                "type": "image",
                "name": "活动图",
                "available_to_send": True,
            },
        ],
        "first_day_sop_sequence": [
            {"source_id": "sop-pack:effect", "mapped_scene": "effect_proof"},
            {"source_id": "sop-pack:activity", "mapped_scene": "activity_intro"},
        ],
        "available_sources_by_scene": {
            "effect_proof": [{"source_id": "sop-pack:effect"}],
            "activity_intro": [{"source_id": "sop-pack:activity"}],
        },
    }


def test_model_asset_context_exposes_options_without_urls_and_marks_recent_delivery() -> None:
    playbook = {
        "items": [
            {
                "content_id": "distance-proof",
                "applicable_scene": "客户觉得门店远",
                "blocker_type": "distance",
                "reply_messages": [
                    {"type": "image", "content": {"url": "https://cdn.example/a.jpg"}},
                    {"type": "image", "content": {"url": "https://cdn.example/b.jpg"}},
                ],
            }
        ]
    }
    catalog = build_appointment_blocker_asset_catalog(playbook)
    context = build_outreach_asset_model_context(
        catalog,
        {"urls": ["https://cdn.example/a.jpg"]},
    )

    assert context["summary"] == {
        "total_count": 2,
        "available_count": 1,
        "recently_sent_count": 1,
        "image_count": 2,
        "video_count": 0,
        "source_count": 1,
        "available_source_count": 1,
    }
    assert all("url" not in item for item in context["items"])
    assert {item["delivery_status"] for item in context["items"]} == {
        "available",
        "recently_sent",
    }
    assert {item["source_id"] for item in context["items"]} == {
        "appointment-blocker:distance-proof"
    }


def test_writer_receives_all_media_options_for_each_selected_source() -> None:
    scene = _scene_analysis()
    scene["required_assets"]["step1"] = {
        "strategy": "configured_image",
        "asset_id": "sop-pack:effect:2",
        "reason": "效果图",
    }
    payload = _first_day_writer_payload(_snapshot(), scene)
    writer = payload["writer_context"]

    assert len(writer["selected_assets"]) == 3
    assert writer["delivery_contract"]["step1"]["media_will_be_sent"] is True
    assert writer["delivery_contract"]["step1"]["planned_asset_ids"] == [
        "sop-pack:effect:2",
        "sop-pack:effect:3",
    ]
    assert writer["delivery_contract"]["step2"]["planned_asset_ids"] == [
        "sop-pack:activity:2"
    ]


def test_effect_scene_without_real_media_is_a_contract_error() -> None:
    scene = _scene_analysis()
    error = _first_day_scene_analysis_error(scene, source_snapshot=_snapshot())
    assert error == "scene analysis required_assets.step1 must attach real effect media"


def test_outreach_log_observability_connects_decision_materials_nodes_and_tasks() -> None:
    scene = _scene_analysis()
    scene["required_assets"]["step1"] = {
        "strategy": "configured_image",
        "asset_id": "sop-pack:effect:2",
        "reason": "效果图",
    }
    run = {
        "workflow_run_id": "run-1",
        "status": "created",
        "reason_code": "first_task_sent",
        "final_decision": "send_pending",
        "input_snapshot": _snapshot(),
        "workflow": {
            "scene_analyst": {
                "input": {"source_snapshot": _snapshot()},
                "output": scene,
                "trace": {
                    "prompt_version": "v15",
                    "elapsed_ms": 123,
                    "attempt_count": 1,
                    "model_usage": {"winner_model": "deepseek-chat"},
                },
            },
            "summary": {"scene_analysis": scene},
        },
        "final_plan": {"steps": []},
        "tasks": [
            {
                "step_index": 1,
                "status": "sent",
                "reply_messages": [
                    {"type": "text", "content": {"text": "您看下这个效果参考"}},
                    {"type": "image", "content": {"url": "https://cdn.example/a.jpg"}},
                ],
            }
        ],
        "events": [],
    }

    summary = build_first_day_run_business_summary(run)
    view = build_first_day_run_observability(run)

    assert summary["last_customer_message"] == "这个效果怎么样"
    assert summary["available_media_count"] == 2
    assert view["decision"]["customer_need"] == "了解效果"
    assert view["materials"]["steps"][0]["delivery_state"] == "sent"
    assert view["materials"]["steps"][0]["planned_media_count"] == 1
    assert view["workflow_nodes"][0]["model"] == "deepseek-chat"
    assert any(
        node["key"] == "scene_analyst_schema_repair_2"
        for node in view["workflow_nodes"]
    )


def test_outreach_log_derives_source_for_historical_asset_rows() -> None:
    scene = _scene_analysis()
    snapshot = _snapshot()
    snapshot["asset_catalog"] = [
        {
            "asset_id": "sop-pack:effect:2",
            "type": "image",
            "name": "历史效果图",
        }
    ]
    view = build_first_day_run_observability(
        {
            "input_snapshot": snapshot,
            "workflow": {"summary": {"scene_analysis": scene}},
            "tasks": [],
        }
    )

    assert view["materials"]["steps"][0]["available_asset_count"] == 1


def test_prompts_bind_visual_language_to_actual_delivery_contract() -> None:
    assert "delivery_contract" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "media_will_be_sent=false" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "media_will_be_sent=false" in FIRST_DAY_CONTRACT_VERIFIER_PROMPT
