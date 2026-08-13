from __future__ import annotations

import json
from pathlib import Path

from app.graph.nodes.parallel_reply_chain import _tool_planner_shared_context
from app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT
from app.prompts.sop_chat_gate import PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
from app.graph.nodes.reply_quality import collect_reply_soft_warnings
from app.services.model_led_objection_playbook_service import ModelLedObjectionPlaybookService
from app.services.sop_execution_service import (
    SopExecutionService,
    _chat_selector_input,
    _parallel_content_gate_output_violations,
)
from app.schemas import ChatRequest


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_PATH = ROOT / "config" / "v2_model_led_objection_playbook.json"
AUDIT_PATH = ROOT / "docs" / "reports" / "v2_appointment_blocker_distillation_audit_20260811.json"
CONCERN_PROVENANCE_FIXTURE = ROOT / "workflow_tests" / "fixtures" / "v2_concern_provenance_cases_20260812.json"


class _EmptyPackService:
    def load(self) -> dict:
        return {"version": 1, "packs": []}


def _service() -> ModelLedObjectionPlaybookService:
    return ModelLedObjectionPlaybookService(PLAYBOOK_PATH)


def test_distillation_imports_all_sources_and_keeps_every_asset_pending() -> None:
    config = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

    assert audit["imported_record_count"] == 104
    assert audit["unique_scene_count"] == 14
    assert len(audit["records"]) == 104
    assert len({item["source_id"] for item in audit["records"]}) == 104
    assert {item["source_id"] for item in audit["records"]} == {
        f"YYHF-{index:04d}" for index in range(1, 105)
    }
    assert all(item["source_id"].startswith("YYHF-") for item in audit["records"])
    assert len(config["sales_principles"]) == 10
    assert len(config["evidence_strategies"]) == 4
    assert len(config["assets"]) == 74
    assert all(item["review_status"] == "pending_review" for item in config["assets"])


def test_runtime_catalog_never_exposes_raw_replies_or_pending_media() -> None:
    service = _service()
    gate_assets = service.gate_assets()
    runtime_principles = service.sales_principles()
    serialized = json.dumps(
        {"gate_assets": gate_assets, "sales_principles": runtime_principles},
        ensure_ascii=False,
    )

    assert len(gate_assets) == 4
    assert all(item["content_type"] == "evidence_strategy" for item in gate_assets)
    assert all(item["messages"] == [] for item in gate_assets)
    assert all("source_ids" not in item for item in gate_assets)
    assert all(set(item) == {"id", "reasoning"} for item in runtime_principles)
    assert "reply_messages" not in serialized
    assert "YYHF-" not in serialized
    assert "好评实拍" not in serialized
    assert "安排同款专家老师" not in serialized


def test_v2_content_catalog_contains_guidance_but_no_unreviewed_media() -> None:
    service = SopExecutionService(
        repository=object(),
        sop_reply_pack_service=_EmptyPackService(),
        model_client=object(),
        model_led_objection_playbook_service=_service(),
    )

    catalog = service.reply_chain_content_catalog()
    items = catalog["sop_packs"]

    assert len(items) == 4
    assert all(item["content_type"] == "evidence_strategy" for item in items)
    assert len(catalog["sales_principles"]) == 8
    assert not any(item["content_type"] == "reviewed_media" for item in items)


def test_parallel_gate_receives_strategy_guidance_without_legacy_scene_fields() -> None:
    request = ChatRequest(content="有点远，我再想想", customer_id="sim_customer", corp_id="sim_corp")
    assets = _service().gate_assets()
    payload = _chat_selector_input(
        request,
        assets,
        shared_context={
            "current_time": {"iso": "2026-08-11T20:00:00+08:00"},
            "current_message": {"content": "有点远，我再想想"},
            "conversation": [
                {"message_ref": "current_message", "role": "customer", "content": "有点远，我再想想"},
            ],
            "authoritative_facts": {"sop_progress": {"completed_pack_ids": []}},
        },
    )

    assert len(payload["unfinished_sops"]) == 4
    assert all(item["content_type"] == "evidence_strategy" for item in payload["unfinished_sops"])
    assert "selected_scene_id" not in payload
    assert "precision_qa_index" not in payload
    assert "reference_messages" not in json.dumps(payload, ensure_ascii=False)


def test_parallel_gate_enforces_three_candidate_limit() -> None:
    selector_input = {
        "content_assets": [
            {"content_id": f"strategy_{index}", "asset_role": "evidence_strategy"}
                for index in range(4)
        ],
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "我再想想"},
        ],
    }
    selector_output = {
        "candidate_assets": [
            {
                "content_id": f"strategy_{index}",
                "relevance": "supporting",
                "evidence_purpose": "reduce uncertainty",
                "render_strategy": "adaptable",
                "evidence_refs": ["current_message"],
            }
            for index in range(4)
        ]
    }

    assert _parallel_content_gate_output_violations(selector_output, selector_input) == [
        "candidate_assets_exceed_limit"
    ]


def test_tool_planner_input_excludes_sales_and_content_guidance() -> None:
    state = {
        "shared_context": {
            "current_message": {"content": "西安有门店吗"},
            "conversation": [{"message_ref": "current_message", "role": "customer", "content": "西安有门店吗"}],
            "authoritative_facts": {"visible_store_scope": {"count": 2}},
            "content_indexes": {"available_sop": {"sop_packs": [{"content_id": "s10_activity_intro"}]}},
            "sales_guidance": {"principles": [{"id": "one", "reasoning": "sales guidance"}]},
            "rules": {
                "MUST FOLLOW": {"hard": True},
                "AUTHORITATIVE FACTS": {"offer": {}},
                "SALES PRINCIPLES": {"playbook": True},
                "CONTENT ASSET POLICY": {"candidate": True},
                "TOOL FACT BOUNDARIES": {"store": True},
            },
        }
    }

    shared = _tool_planner_shared_context(state)

    assert "content_indexes" not in shared
    assert "sales_guidance" not in shared
    assert set(shared["rules"]) == {"MUST FOLLOW", "AUTHORITATIVE FACTS", "TOOL FACT BOUNDARIES"}


def test_v2_prompts_do_not_restore_scene_matching_or_raw_reference_replies() -> None:
    active_prompts = PARALLEL_CONTENT_GATE_SYSTEM_PROMPT + PARALLEL_REPLY_SYSTEM_PROMPT

    assert "selected_scene_id" not in active_prompts
    assert "appointment_blocker_reference" not in active_prompts
    assert "最终 Reply 销售大脑" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "销冠经验" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不是场景匹配器" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "第一次询价或第一次完整了解活动" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不同轮发预约金卡" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "reference_messages" not in active_prompts
    assert "客户说 X" not in active_prompts


def test_v2_prompts_forbid_inventing_customer_concerns_and_valueless_questions() -> None:
    assert "不主动植入客户没有提出的顾虑" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "直接发，不先问客户要不要看" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "提问只用于获得会改变事实、工具、证据或行动的信息" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_v2_distilled_guidance_requires_explicit_customer_concern() -> None:
    config = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    principles = {item["id"]: item["reasoning"] for item in config["sales_principles"]}
    strategies = {item["id"]: item for item in config["evidence_strategies"]}

    assert "不主动猜测或向客户植入未提出的顾虑" in principles["understand_decision_uncertainty"]
    assert "客户没有提出的副作用、反弹或恢复顾虑不要主动展开" in principles["set_reasonable_expectations"]
    assert "不自行用不能保证、不能说太满或因人而异削弱已批准结论" in principles["set_reasonable_expectations"]
    assert "客户明确询问" in strategies["strategy_outcome_confidence"]["customer_uncertainty"]
    assert "客户明确询问" in strategies["strategy_value_and_price"]["customer_uncertainty"]
    assert any(
        "不要继续围着距离争辩" in item
        for item in strategies["strategy_travel_effort"]["reasoning_moves"]
    )
    assert any(
        "首次询价只讲活动，不发卡" in item
        for item in strategies["strategy_value_and_price"]["reasoning_moves"]
    )
    time_moves = strategies["strategy_time_and_priority"]["reasoning_moves"]
    assert any("不重复活动价格、案例或门店" in item for item in time_moves)
    assert any("尚未交付" in item and "降低行动成本" in item for item in time_moves)
    assert any("旧活动或旧价格不算新价值" in item for item in time_moves)
    runtime_principles = {item["id"]: item["reasoning"] for item in config["runtime_sales_principles"]}
    assert len(runtime_principles) == 8
    assert "不循环辩解" in runtime_principles["acknowledge_then_switch"]


def test_v2_concern_provenance_fixture_covers_both_suppression_and_explicit_questions() -> None:
    fixture = json.loads(CONCERN_PROVENANCE_FIXTURE.read_text(encoding="utf-8"))
    scenarios = {item["id"]: item for item in fixture["scenarios"]}

    assert len(scenarios) == 6
    assert "隐形收费" in scenarios["trust_doubt_does_not_invent_fee_concern"]["expected"]["forbidden_phrases"]
    assert "手和脸" in scenarios["face_total_price_does_not_invent_scope_ambiguity"]["expected"]["forbidden_phrases"]
    assert "explicit_hidden_fee_question_still_answered" in scenarios
    assert "explicit_rebound_question_still_answered" in scenarios
    assert "explicit_hand_face_scope_still_answered" in scenarios


def test_v2_does_not_apply_legacy_precision_mainline_warning() -> None:
    messages = [{"type": "text", "content": "做完后注意基础防晒和补水就可以。"}]
    state = {
        "evidence_join": {"schema_version": "reply_chain_evidence_join_v1"},
        "precision_qa_decision": {"question_id": "aftercare_guidance", "confidence": "high"},
        "sales_progression": {"status": "continue", "target_stage": "activity"},
    }

    warnings = collect_reply_soft_warnings(messages, state)

    assert warnings == []
