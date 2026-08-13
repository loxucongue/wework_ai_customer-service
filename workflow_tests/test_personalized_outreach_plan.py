from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.outreach_service import (
    OutreachService,
    _compose_outreach_messages,
    _conversation_activity_from_context,
    _first_day_activity_sop_payment_step,
    _first_day_configured_assets_for_step,
    _first_day_final_plan_error,
    _first_day_materialized_sop_messages,
    _first_day_outreach_plan_error,
    _first_day_scene_analysis_error,
    _first_day_available_sources_by_scene,
    _first_day_scene_lock_error,
    _first_day_verifier_error,
    _first_day_writer_payload,
    _normalize_first_day_outreach_schedule,
    _normalize_first_day_repaired_plan,
    _normalize_first_day_scene_analysis,
    _normalize_outreach_plan_response,
    _normalize_outreach_schedule,
    _outreach_plan_context_error,
    _outreach_plan_structure_error,
    build_outreach_activity_quote_fact,
)
from app.services.outreach_first_day_prompts import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT,
    FIRST_DAY_SCENE_ANALYST_PROMPT,
)
from app.services.outreach_assets import (
    appointment_blocker_materials,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
    enrich_recent_outreach_media,
)
from app.services.sop_platform_task_policy import personalized_payment_collection_eligibility


class PersonalizedOutreachPlanTests(unittest.IsolatedAsyncioTestCase):
    def test_first_day_sources_are_explicitly_scoped_by_scene(self) -> None:
        snapshot = {
            "first_day_sop_sequence": [
                {"source_id": "sop-pack:store", "mapped_scene": "store_area_request"},
                {"source_id": "sop-pack:deposit", "mapped_scene": "deposit_close"},
            ],
            "appointment_blocker_scene_index": [
                {
                    "applicable_scene": "客户担心效果",
                    "blocker_types": ["效果顾虑"],
                    "source_ids": ["appointment-blocker:effect"],
                }
            ],
        }

        available = _first_day_available_sources_by_scene(snapshot)

        self.assertEqual(available["store_area_request"][0]["source_id"], "sop-pack:store")
        self.assertEqual(available["deposit_close"][0]["source_id"], "sop-pack:deposit")
        self.assertEqual(
            available["trust_repair"][0]["source_id"],
            "appointment-blocker:effect",
        )
        self.assertTrue(available["trust_repair"][0]["requires_customer_evidence"])
    async def test_first_day_model_node_retries_one_timeout_and_records_trace(self) -> None:
        class _TimeoutThenSuccessModel:
            def __init__(self) -> None:
                self.calls = 0
                self.last_usage = {"model": "test"}

            async def chat_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                self.calls += 1
                if self.calls <= 2:
                    raise TimeoutError("total timeout 40.0s")
                return {"ok": True}

        service = OutreachService.__new__(OutreachService)
        service.model_client = _TimeoutThenSuccessModel()

        response, trace = await service._run_first_day_model_node(
            node="scene_analyst",
            prompt="测试提示词",
            prompt_version="test-v1",
            payload={"value": 1},
        )

        self.assertEqual(response, {"ok": True})
        self.assertEqual(service.model_client.calls, 3)
        self.assertEqual(trace["attempt_count"], 3)
        self.assertEqual(
            [item["status"] for item in trace["attempts"]],
            ["timeout", "timeout", "completed"],
        )

    async def test_first_day_model_node_does_not_retry_non_timeout_errors(self) -> None:
        class _InvalidModel:
            def __init__(self) -> None:
                self.calls = 0

            async def chat_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                self.calls += 1
                raise ValueError("invalid json")

        service = OutreachService.__new__(OutreachService)
        service.model_client = _InvalidModel()

        with self.assertRaises(ValueError):
            await service._run_first_day_model_node(
                node="scene_analyst",
                prompt="测试提示词",
                prompt_version="test-v1",
                payload={"value": 1},
            )
        self.assertEqual(service.model_client.calls, 1)

    def test_first_day_prompts_have_separate_roles_and_locked_contracts(self) -> None:
        analyst = " ".join(FIRST_DAY_SCENE_ANALYST_PROMPT.split())
        writer = " ".join(FIRST_DAY_PLAN_WRITER_PROMPT.split())
        verifier = " ".join(FIRST_DAY_CONTRACT_VERIFIER_PROMPT.split())

        self.assertIn("绝不撰写任何客户可见话术", analyst)
        self.assertIn("场景枚举", analyst)
        self.assertIn("只有文字效果说明不等于已经交付图片证据", analyst)
        self.assertIn("首日主动唤醒永远不发送 `payment_collection`", analyst)
        self.assertIn("personalized_order_gate", analyst)
        self.assertIn("historical_order_expired_new_cycle", analyst)
        self.assertIn("禁止误抑制", analyst)
        self.assertIn("time_deposit_objection", analyst)
        self.assertIn("out_of_scope_pullback", analyst)
        self.assertIn("scene_contract` 是不可更改的权威合同", writer)
        self.assertIn("轻过渡 + 有效场景内容", writer)
        self.assertIn("15 至 20 分钟", writer)
        self.assertIn("禁止推断或提及客户性别", writer)
        self.assertIn("不要主动强调原价金额", writer)
        self.assertIn("到店抵扣，未做或不满意可退，实际按付款记录核对", writer)
        self.assertIn("优先保留 SOP 包原有消息顺序和结构", writer)
        self.assertIn("一条或多条非空 `reply_messages`", writer)
        self.assertIn("不得重新规划业务场景", verifier)
        self.assertIn("语义上重复", verifier)
        self.assertIn("pass|repair|block", verifier)
        self.assertIn("允许以前一步 `activity_intro` 作为本计划内报价证据", verifier)
        self.assertIn("不得因为超过两句就要求修复", verifier)
        self.assertIn("已询问、等待回答", analyst)
        self.assertIn("不能只看 SOP 完成标记", analyst)
        self.assertIn("判定位置重复前必须逐条核对消息角色", verifier)
        self.assertIn("客户本人问", verifier)
        self.assertIn("不得凭主观理解把正确的 `scene` 判成另一个场景", verifier)
        for prompt in (analyst, writer, verifier):
            self.assertNotIn("# 1. Role", prompt)
            self.assertNotIn("# 2. Objective", prompt)
            self.assertNotIn("Output Contract", prompt)

    def test_appointment_blockers_supply_model_context_and_resolvable_assets(self) -> None:
        config = {
            "items": [
                {
                    "content_id": "YYHF-0001",
                    "blocker_type": "效果顾虑",
                    "applicable_scene": "客户担心实际效果",
                    "reply_messages": [
                        {"type": "text", "content": "效果参考"},
                        {"type": "image", "content": "https://oss.example/effect.png"},
                    ],
                },
            ]
        }

        materials = appointment_blocker_materials(config)
        assets = build_appointment_blocker_asset_catalog(config)
        scene_index = build_appointment_blocker_scene_index(config)

        self.assertEqual([item["content_id"] for item in materials], ["YYHF-0001"])
        self.assertEqual(scene_index[0]["source_ids"], ["appointment-blocker:YYHF-0001"])
        self.assertEqual(assets[0]["asset_id"], "appointment-blocker:YYHF-0001:2")
        self.assertEqual(assets[0]["url"], "https://oss.example/effect.png")
        self.assertEqual(assets[0]["source"], "appointment_blocker_playbook")

        recent = enrich_recent_outreach_media(
            {"urls": ["https://oss.example/effect.png"], "document_ids": []},
            assets,
        )
        match = recent["items"][0]["configured_matches"][0]
        self.assertEqual(match["asset_id"], "appointment-blocker:YYHF-0001:2")
        self.assertEqual(match["name"], "YYHF-0001")
        self.assertEqual(match["annotation"], "客户担心实际效果")
        self.assertEqual(
            recent["configured_deliveries"][0]["asset_id"],
            "appointment-blocker:YYHF-0001:2",
        )

    def test_first_day_sop_sequence_supplies_ordered_pack_context_and_assets(self) -> None:
        class _SopReplyPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_need_and_case",
                            "enabled": True,
                            "scopes": ["chat_gate"],
                            "sop_category": "s10_need_and_case",
                            "name": "介绍效果",
                            "purpose": "发送真实效果参考",
                            "order": 20,
                            "day_stage": "day1",
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "亲，您看这个效果参考。"}},
                                {"type": "image", "order": 2, "content": {"url": "https://oss.example/effect.png"}},
                            ],
                        },
                        {
                            "id": "s10_store_prompt",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "store_prompt",
                            "name": "轻度询问城市",
                            "purpose": "询问客户方便到店区域",
                            "order": 35,
                            "day_stage": "day1",
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "亲，您在哪个城市哪个区呢？"}},
                            ],
                        },
                        {
                            "id": "s10_activity_intro",
                            "enabled": True,
                            "scopes": ["chat_gate"],
                            "sop_category": "s10_activity_intro",
                            "name": "活动介绍",
                            "purpose": "完整介绍活动和参与方式",
                            "order": 30,
                            "day_stage": "day1",
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "完整活动规则"}},
                                {"type": "image", "order": 2, "content": {"url": "https://oss.example/activity.png"}},
                                {"type": "text", "order": 3, "content": {"text": "到店抵扣退款说明"}},
                                {"type": "payment_collection", "order": 4, "content": {"amount": 10}},
                            ],
                        },
                    ]
                }

        service = OutreachService.__new__(OutreachService)
        service.sop_reply_pack_service = _SopReplyPackService()

        sequence = service._first_day_sop_sequence()
        self.assertEqual(
            [item["pack_id"] for item in sequence],
            ["s10_need_and_case", "s10_activity_intro", "s10_store_prompt"],
        )
        self.assertEqual(sequence[0]["source_id"], "sop-pack:s10_need_and_case")
        self.assertEqual(sequence[0]["mapped_scene"], "effect_proof")
        self.assertEqual(sequence[1]["source_id"], "sop-pack:s10_activity_intro")
        self.assertEqual(sequence[1]["mapped_scene"], "activity_intro")
        self.assertEqual(
            [message["type"] for message in sequence[1]["reply_messages"]],
            ["text", "image", "text", "payment_collection"],
        )
        self.assertEqual(sequence[2]["mapped_scene"], "store_area_request")
        self.assertEqual(sequence[0]["reply_messages"][1]["asset_id"], "sop-pack:s10_need_and_case:2")

        assets = service._first_day_sop_asset_catalog(sequence)
        self.assertEqual(assets[0]["asset_id"], "sop-pack:s10_need_and_case:2")
        self.assertEqual(assets[0]["url"], "https://oss.example/effect.png")
        self.assertEqual(assets[0]["source"], "first_day_sop_pack")

        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="store_area_request",
        )
        analysis["precedence_decision"] = {
            "row_id": "no_blocker_sop_progression",
            "message_indexes": [0],
            "reason": "客户无明确卡点，按首日 SOP 顺序推进。",
        }
        analysis["selected_source_ids"] = {
            "step1": ["sop-pack:s10_need_and_case", "sop-pack:s10_need_and_case:2"],
            "step2": ["sop-pack:s10_store_prompt"],
        }
        analysis["required_assets"]["step1"] = {
            "strategy": "configured_image",
            "asset_id": "sop-pack:s10_need_and_case:2",
            "reason": "效果 SOP 包包含真实图片。",
        }

        self.assertEqual(
            _first_day_scene_analysis_error(
                analysis,
                source_snapshot={
                    "recent_messages": [{"direction": "customer", "content": "你好"}],
                    "first_day_sop_sequence": sequence,
                    "asset_catalog": assets,
                    "payment_collection_gate": {"eligible": False},
                },
            ),
            "",
        )

        alias_analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        alias_analysis["selected_source_ids"] = {
            "step1": ["sop-pack:effect_proof"],
            "step2": ["sop-pack:activity_intro"],
        }
        normalized_alias = _normalize_first_day_scene_analysis(
            alias_analysis,
            message_count=1,
            source_snapshot={
                "recent_messages": [{"direction": "customer", "content": "你好"}],
                "first_day_sop_sequence": [
                    *sequence,
                    {
                        "source_id": "sop-pack:s10_activity_intro",
                        "pack_id": "s10_activity_intro",
                        "sop_category": "s10_activity_intro",
                        "mapped_scene": "activity_intro",
                        "reply_messages": [
                            {"type": "text", "order": 1, "text": "完整活动包"},
                            {"type": "image", "order": 2, "asset_id": "sop-pack:s10_activity_intro:2"},
                        ],
                    },
                ],
                "asset_catalog": [
                    *assets,
                    {
                        "asset_id": "sop-pack:s10_activity_intro:2",
                        "type": "image",
                        "url": "https://oss.example/activity.png",
                    },
                ],
                "payment_collection_gate": {"eligible": False},
            },
        )
        self.assertEqual(
            normalized_alias["selected_source_ids"]["step1"],
            ["sop-pack:s10_need_and_case"],
        )
        self.assertEqual(
            normalized_alias["selected_source_ids"]["step2"],
            ["sop-pack:s10_activity_intro"],
        )
        self.assertEqual(
            normalized_alias["required_assets"]["step2"]["asset_id"],
            "sop-pack:s10_activity_intro:2",
        )

    def test_first_day_required_sop_context_fails_closed(self) -> None:
        class _BrokenSopReplyPackService:
            def load(self) -> dict[str, Any]:
                raise OSError("temporary config failure")

        service = OutreachService.__new__(OutreachService)
        service.sop_reply_pack_service = _BrokenSopReplyPackService()

        with self.assertRaisesRegex(RuntimeError, "first_day_sop_context_load_failed"):
            service._first_day_sop_sequence(required=True)

        class _IncompleteSopReplyPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_store_prompt",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "store_prompt",
                            "reply_messages": [
                                {"type": "text", "content": {"text": "您在哪个城市？"}},
                            ],
                        }
                    ]
                }

        service.sop_reply_pack_service = _IncompleteSopReplyPackService()
        with self.assertRaisesRegex(
            RuntimeError,
            "first_day_sop_context_incomplete: missing_scenes=activity_intro,effect_proof",
        ):
            service._first_day_sop_sequence(required=True)

    def test_first_day_scene_contract_rejects_duplicate_scenes_and_non_activity_payment(self) -> None:
        snapshot = {
            "recent_messages": [{"direction": "customer", "content": "想看看效果"}],
            "asset_catalog": [],
            "payment_collection_gate": {"eligible": False},
        }
        duplicate = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="effect_proof",
        )
        self.assertEqual(
            _first_day_scene_analysis_error(duplicate, source_snapshot=snapshot),
            "first-day scene analysis must select two different scenes",
        )

        payment = _first_day_scene_analysis(
            step1_scene="deposit_close",
            step2_scene="trust_repair",
        )
        payment["payment_action"] = {"step": 1, "allowed": True, "reason": "customer wants to pay"}
        self.assertEqual(
            _first_day_scene_analysis_error(payment, source_snapshot=snapshot),
            "first-day scene analysis cannot authorize payment_collection",
        )

        external_case_search = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="trust_repair",
        )
        external_case_search["required_assets"]["step1"] = {
            "strategy": "case_search",
            "asset_id": "",
            "reason": "临时查询案例",
        }
        self.assertIn(
            "must use media from the selected SOP or appointment-blocker source",
            _first_day_scene_analysis_error(external_case_search, source_snapshot=snapshot),
        )

        completed_scene = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        completed_scene["scene_completion_matrix"]["effect_proof"]["status"] = "completed"
        self.assertIn(
            "cannot select a scene whose completion status is completed",
            _first_day_scene_analysis_error(completed_scene, source_snapshot=snapshot),
        )

    def test_first_day_scene_analysis_normalizes_explicit_or_inferred_one_based_evidence(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        analysis["message_index_base"] = 1
        analysis["evidence"] = [{"message_index": 2, "fact": "last message"}]
        analysis["delivered_scenes"] = [
            {"scene": "trust_repair", "message_indexes": [1], "asset_ids": [], "evidence": "first"}
        ]

        normalized = _normalize_first_day_scene_analysis(analysis, message_count=2)

        self.assertEqual(normalized["message_index_base"], 0)
        self.assertEqual(normalized["evidence"][0]["message_index"], 1)
        self.assertEqual(normalized["delivered_scenes"][0]["message_indexes"], [0])

    def test_first_day_scene_analysis_does_not_add_card_for_deposit_scene(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="activity_intro",
            step2_scene="deposit_close",
        )
        analysis["payment_action"] = {"step": 0, "allowed": False, "reason": "model omitted"}

        normalized = _normalize_first_day_scene_analysis(
            analysis,
            message_count=0,
            source_snapshot={"payment_collection_gate": {"eligible": True}},
        )

        self.assertEqual(normalized["payment_action"]["step"], 0)
        self.assertFalse(normalized["payment_action"]["allowed"])

    def test_first_day_scene_analysis_normalizes_sop_category_scene_aliases(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="price_quote",
            step2_scene="payment_followup",
        )
        analysis["current_scene"] = "price_quote"
        analysis["payment_action"] = {"step": 0, "allowed": False, "reason": "model omitted"}
        snapshot = {
            "recent_messages": [{"direction": "customer", "content": "你好"}],
            "conversation_activity": {"real_customer_message_count": 1},
            "payment_collection_gate": {"eligible": True},
            "asset_catalog": [],
        }

        normalized = _normalize_first_day_scene_analysis(
            analysis,
            message_count=1,
            source_snapshot=snapshot,
        )

        self.assertEqual(normalized["current_scene"], "activity_intro")
        self.assertEqual(normalized["step1_scene"], "activity_intro")
        self.assertEqual(normalized["step2_scene"], "deposit_close")
        self.assertEqual(normalized["payment_action"]["step"], 0)
        self.assertFalse(normalized["payment_action"]["allowed"])

    def test_first_day_activity_quote_pack_is_the_only_payment_source(self) -> None:
        activity_source = "sop-pack:s10_activity_intro"
        deposit_source = "sop-pack:s10_deposit_close"
        snapshot = {
            "payment_collection_gate": {"eligible": True},
            "first_day_sop_sequence": [
                {
                    "source_id": activity_source,
                    "sop_category": "s10_activity_intro",
                    "mapped_scene": "activity_intro",
                    "reply_messages": [
                        {"type": "text", "order": 1, "text": "完整活动介绍"},
                        {"type": "payment_collection", "order": 2, "amount": 10},
                    ],
                },
                {
                    "source_id": deposit_source,
                    "sop_category": "deposit_push",
                    "mapped_scene": "deposit_close",
                    "reply_messages": [
                        {"type": "text", "order": 1, "text": "预约金文字推进"},
                        {"type": "payment_collection", "order": 2, "amount": 10},
                    ],
                },
            ],
        }
        activity = _first_day_scene_analysis(
            step1_scene="activity_intro",
            step2_scene="deposit_close",
        )
        activity["selected_source_ids"] = {
            "step1": [activity_source],
            "step2": [deposit_source],
        }
        self.assertEqual(_first_day_activity_sop_payment_step(activity, snapshot), 1)

        deposit_only = _first_day_scene_analysis(
            step1_scene="deposit_close",
            step2_scene="trust_repair",
        )
        deposit_only["selected_source_ids"] = {
            "step1": [deposit_source],
            "step2": [],
        }
        self.assertEqual(_first_day_activity_sop_payment_step(deposit_only, snapshot), 0)

    def test_first_day_scene_analysis_requires_one_main_source_and_matching_media(self) -> None:
        sop_source = "sop-pack:effect"
        blocker_source = "appointment-blocker:YYHF-0001"
        snapshot = {
            "recent_messages": [{"direction": "customer", "content": "效果怎么样"}],
            "payment_collection_gate": {"eligible": False},
            "first_day_sop_sequence": [
                {
                    "source_id": sop_source,
                    "mapped_scene": "effect_proof",
                    "reply_messages": [],
                }
            ],
            "appointment_blocker_scene_index": [
                {"source_ids": [blocker_source]},
            ],
            "asset_catalog": [
                {
                    "asset_id": f"{sop_source}:2",
                    "type": "image",
                    "url": "https://cdn.example/sop.jpg",
                },
                {
                    "asset_id": f"{blocker_source}:2",
                    "type": "image",
                    "url": "https://cdn.example/blocker.jpg",
                },
            ],
        }
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="trust_repair",
        )
        analysis["selected_source_ids"] = {
            "step1": [sop_source, blocker_source],
            "step2": [blocker_source],
        }
        self.assertIn(
            "must select exactly one main SOP or appointment-blocker source",
            _first_day_scene_analysis_error(analysis, source_snapshot=snapshot),
        )

        analysis["selected_source_ids"] = {
            "step1": [sop_source, f"{blocker_source}:2"],
            "step2": [blocker_source],
        }
        self.assertIn(
            "contains media outside the selected main source",
            _first_day_scene_analysis_error(analysis, source_snapshot=snapshot),
        )

        analysis["precedence_decision"]["row_id"] = "no_blocker_sop_progression"
        analysis["selected_source_ids"] = {
            "step1": [sop_source],
            "step2": [blocker_source],
        }
        self.assertIn(
            "must use a main SOP source",
            _first_day_scene_analysis_error(analysis, source_snapshot=snapshot),
        )

    def test_first_day_scene_analysis_tolerates_mixed_zero_and_one_based_indexes(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        analysis["message_index_base"] = 1
        analysis["scene_completion_matrix"]["effect_proof"]["message_indexes"] = [0, 1, 3, 4]
        analysis["writer_context_message_indexes"] = [0, 2, 3, 9]

        normalized = _normalize_first_day_scene_analysis(analysis, message_count=3)

        self.assertEqual(
            normalized["scene_completion_matrix"]["effect_proof"]["message_indexes"],
            [0, 2],
        )
        self.assertEqual(normalized["writer_context_message_indexes"], [0, 1, 2])
        self.assertEqual(
            _first_day_scene_analysis_error(
                normalized,
                source_snapshot={"recent_messages": [{}, {}, {}], "asset_catalog": []},
            ),
            "",
        )

    def test_first_day_scene_analysis_deterministically_suppresses_unopened_customer(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        snapshot = {
            "recent_messages": [{"direction": "staff", "content": "企微自动开场"}],
            "conversation_activity": {"real_customer_message_count": 0},
            "asset_catalog": [],
        }

        normalized = _normalize_first_day_scene_analysis(
            analysis,
            message_count=1,
            source_snapshot=snapshot,
        )

        self.assertFalse(normalized["eligible"])
        self.assertEqual(normalized["step1_scene"], "suppress")
        self.assertEqual(normalized["step2_scene"], "suppress")
        self.assertEqual(
            _first_day_scene_analysis_error(normalized, source_snapshot=snapshot),
            "",
        )

    def test_first_day_scene_analysis_defaults_missing_non_payment_action(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="trust_repair",
            step2_scene="objection_resolution",
        )
        analysis.pop("payment_action")
        snapshot = {
            "recent_messages": [{"direction": "customer"}, {"direction": "staff"}],
            "conversation_activity": {"real_customer_message_count": 1},
            "payment_collection_gate": {"eligible": False},
            "asset_catalog": [],
        }

        normalized = _normalize_first_day_scene_analysis(
            analysis,
            message_count=2,
            source_snapshot=snapshot,
        )

        self.assertEqual(normalized["payment_action"]["step"], 0)
        self.assertFalse(normalized["payment_action"]["allowed"])
        self.assertEqual(
            _first_day_scene_analysis_error(normalized, source_snapshot=snapshot),
            "",
        )

    def test_first_day_verifier_contract_and_scene_lock_are_structural_boundaries(self) -> None:
        plan = _ModelClient().response
        plan["steps"][0].update({"delay_minutes": 0, "scene": "store_area_request"})
        plan["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "trust_repair"}
        )
        scene_analysis = _first_day_scene_analysis(
            step1_scene="store_area_request",
            step2_scene="trust_repair",
        )
        self.assertEqual(
            _first_day_scene_lock_error(plan, scene_analysis=scene_analysis),
            "",
        )
        self.assertEqual(
            _first_day_verifier_error(
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                    "verified_plan": plan,
                }
            ),
            "first-day verifier must not return customer plan content",
        )
        self.assertEqual(
            _first_day_verifier_error(
                {
                    "decision": "repair",
                    "block_category": "none",
                    "violations": [
                        {"code": "repeat", "field": "steps.0", "evidence": "与历史重复"}
                    ],
                    "repair_instructions": [],
                }
            ),
            "repair verifier response requires violations and repair instructions",
        )
        self.assertIn(
            "immutable contract fields",
            _first_day_verifier_error(
                {
                    "decision": "repair",
                    "block_category": "none",
                    "violations": [
                        {
                            "code": "scene_conflict",
                            "field": "candidate_plan.steps[1].scene",
                            "evidence": "错误地认为第二步场景不一致",
                        }
                    ],
                    "repair_instructions": [
                        {
                            "field": "candidate_plan.steps[1].scene",
                            "instruction": "修改第二步场景",
                        }
                    ],
                }
            ),
        )
        plan["steps"][1]["scene"] = "store_area_request"
        self.assertEqual(
            _first_day_scene_lock_error(plan, scene_analysis=scene_analysis),
            "first-day plan scenes must exactly match the scene analysis contract",
        )
        self.assertEqual(
            _first_day_verifier_error(
                {
                    "decision": "block",
                    "block_category": "source_hard_boundary",
                    "violations": [
                        {"code": "health", "field": "source_snapshot", "evidence": "健康风险"}
                    ],
                    "repair_instructions": [],
                }
            ),
            "",
        )

    def test_first_day_writer_payload_only_contains_selected_context_and_materials(self) -> None:
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )
        analysis["writer_context_message_indexes"] = [1, 2]
        analysis["selected_source_ids"] = {
            "step1": ["appointment-blocker:YYHF-0001"],
            "step2": ["appointment-blocker:YYHF-0002"],
        }
        analysis["required_assets"]["step1"] = {
            "strategy": "configured_image",
            "asset_id": "effect-image",
            "reason": "发送效果图",
        }
        payload = _first_day_writer_payload(
            {
                "recent_messages": [
                    {"direction": "staff", "content": "无关旧消息"},
                    {"direction": "customer", "content": "效果怎么样"},
                    {"direction": "staff", "content": "只有文字说明"},
                ],
                "asset_catalog": [
                    {"asset_id": "effect-image", "type": "image"},
                    {"asset_id": "unused-image", "type": "image"},
                ],
            },
            analysis,
            appointment_material_catalog=[
                {"source_id": "appointment-blocker:YYHF-0001", "reply_messages": []},
                {"source_id": "appointment-blocker:YYHF-0002", "reply_messages": []},
                {"source_id": "appointment-blocker:YYHF-9999", "reply_messages": []},
            ],
        )

        writer_context = payload["writer_context"]
        self.assertEqual(
            [message["message_index"] for message in writer_context["recent_messages"]],
            [1, 2],
        )
        self.assertEqual(
            {item.get("source_id") for item in writer_context["selected_materials"]},
            {"appointment-blocker:YYHF-0001", "appointment-blocker:YYHF-0002"},
        )
        self.assertEqual(
            [item["asset_id"] for item in writer_context["selected_assets"]],
            ["effect-image"],
        )

    def test_first_day_final_contract_rejects_near_repeat_before_plan_creation(self) -> None:
        plan = _ModelClient().response
        plan["steps"][0].update({"delay_minutes": 0, "scene": "effect_proof"})
        plan["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "activity_intro"}
        )
        repeated = "亲，到店会先看斑点情况和适合的方向，合适再决定，您主要是哪类斑点呢？"
        plan["steps"][0]["reply_messages"][0]["content"]["text"] = repeated
        analysis = _first_day_scene_analysis(
            step1_scene="effect_proof",
            step2_scene="activity_intro",
        )

        error = _first_day_final_plan_error(
            plan,
            scene_analysis=analysis,
            source_snapshot={
                "recent_messages": [{"direction": "staff", "content": repeated}],
                "recent_sop_delivery": [],
            },
        )

        self.assertIn("first_day_message_too_similar_to_history", error)

    def test_first_day_structure_rejects_long_term_second_step_delay(self) -> None:
        response = _ModelClient().response
        response["steps"] = [dict(response["steps"][0]), dict(response["steps"][1])]
        response["steps"][0]["delay_minutes"] = 0
        response["steps"][1]["delay_minutes"] = 1440

        self.assertEqual(
            _first_day_outreach_plan_error(response),
            "first-day second step delay_minutes must be between 15 and 20",
        )

    def test_first_day_structure_allows_no_final_cta_but_never_non_text_messages(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["delay_minutes"] = 0
        response["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "cta": "none"}
        )
        self.assertEqual(_first_day_outreach_plan_error(response), "")

        response["steps"][0]["reply_messages"][0]["type"] = "object"
        self.assertEqual(
            _first_day_outreach_plan_error(response),
            "plan step reply_messages must contain non-empty text items",
        )

    def test_first_day_structure_rejects_payment_card_even_when_order_gate_is_ready(self) -> None:
        response = _ModelClient().response
        response["steps"] = [dict(response["steps"][0]), dict(response["steps"][1])]
        response["steps"][0].update(
            {
                "delay_minutes": 0,
                "content_mode": "transaction",
                "should_send_payment_collection": True,
                "payment_collection_basis": "model_selected_after_quote",
            }
        )
        response["steps"][1].update(
            {
                "delay_minutes": 18,
                "content_mode": "value_only",
                "should_send_payment_collection": False,
            }
        )

        self.assertEqual(
            _first_day_outreach_plan_error(response),
            "first-day outreach cannot send payment_collection",
        )

    def test_first_day_payment_card_is_rejected_before_content_mode_checks(self) -> None:
        response = _ModelClient().response
        response["steps"] = [dict(response["steps"][0]), dict(response["steps"][1])]
        response["steps"][0].update(
            {
                "delay_minutes": 0,
                "content_mode": "soft_conversion",
                "should_send_payment_collection": True,
                "payment_collection_basis": "model_selected_after_quote",
            }
        )
        response["steps"][1]["delay_minutes"] = 18

        self.assertEqual(
            _first_day_outreach_plan_error(response),
            "first-day outreach cannot send payment_collection",
        )

    def test_first_day_repair_normalizer_removes_model_media_placeholders_and_cards(self) -> None:
        response = _ModelClient().response
        response["steps"] = [dict(response["steps"][0]), dict(response["steps"][1])]
        response["steps"][0]["reply_messages"] = [
            {"type": "text", "order": 1, "content": {"text": "第一步文本"}},
            {"type": "image", "order": 2, "content": {"url": "待代码拼装"}},
        ]
        response["steps"][1]["reply_messages"] = [
            {"type": "text", "order": 1, "content": {"text": "可以微信转账或发10元红包预约"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ]
        analysis = _first_day_scene_analysis(
            step1_scene="activity_intro",
            step2_scene="deposit_close",
        )
        analysis["payment_action"] = {"step": 2, "allowed": True, "reason": "模型越权"}

        normalized = _normalize_first_day_repaired_plan(
            response,
            scene_analysis=analysis,
        )

        self.assertEqual(
            [[message["type"] for message in step["reply_messages"]] for step in normalized["steps"]],
            [["text"], ["text"]],
        )
        self.assertFalse(any(step["should_send_payment_collection"] for step in normalized["steps"]))

    def test_realistic_first_day_model_fixtures_use_production_message_shape(self) -> None:
        fixture_paths = sorted(
            Path("workflow_tests/fixtures").glob(
                "outreach_first_day_realistic_*_set_20260806.json"
            )
        )
        cases = []
        for fixture_path in fixture_paths:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            cases.extend(payload["cases"])

        self.assertEqual(len(fixture_paths), 4)
        self.assertGreaterEqual(len(cases), 18)
        self.assertGreaterEqual(sum(len(case["recent_messages"]) for case in cases), 180)
        for case in cases:
            self.assertEqual(
                case["trigger_context"]["trigger_type"],
                "first_day_opened_silence",
            )
            for message in case["recent_messages"]:
                self.assertIn(message["direction"], {"customer", "staff"})
                self.assertIn(message["sender_type"], {"customer", "staff"})
                self.assertTrue(message["msgtype"])
                self.assertTrue(message["created_at"])

    def test_payment_collection_does_not_require_matching_unpaid_platform_order(self) -> None:
        no_order = personalized_payment_collection_eligibility(
            {"source": "platform_agent", "orders": []},
            amount=10,
        )
        matching_order = personalized_payment_collection_eligibility(
            {
                "source": "platform_agent",
                "orders": [
                    {
                        "id": "order-unpaid",
                        "status": "pending",
                        "is_current_order": True,
                        "store_id": "store-101",
                        "prepay_required": 10,
                        "prepay_paid": 0,
                    }
                ],
            },
            amount=10,
        )

        self.assertTrue(no_order["eligible"])
        self.assertEqual(no_order["reason"], "payment_collection_allowed_without_order")
        self.assertTrue(matching_order["eligible"])
        self.assertEqual(matching_order["reason"], "pending_order_payment_allowed")
        self.assertEqual(matching_order["order_id"], "order-unpaid")

    def test_plan_normalizer_wraps_text_content_in_reply_message_object(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["reply_messages"] = [
            {"type": "text", "order": 8, "content": "平时护理温和一点会更稳。"}
        ]

        normalized = _normalize_outreach_plan_response(response)

        self.assertEqual(
            normalized["steps"][0]["reply_messages"],
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "平时护理温和一点会更稳。"},
                }
            ],
        )
        self.assertEqual(_outreach_plan_structure_error(normalized), "")

    def test_plan_structure_accepts_multiple_text_messages_per_step(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["reply_messages"] = [
            {"type": "text", "order": 1, "content": {"text": "平时防晒没跟上的话，色素会更容易显出来。"}},
            {"type": "text", "order": 2, "content": {"text": "日常做点简单遮挡，再把补水跟上会更稳一些。"}},
        ]

        self.assertEqual(_outreach_plan_structure_error(response), "")

        response["steps"][0]["reply_messages"].append(
            {"type": "text", "order": 3, "content": {"text": "按 SOP 包顺序补充第三条也可以。"}}
        )
        self.assertEqual(_outreach_plan_structure_error(response), "")

        response["steps"][0]["reply_messages"] = []
        self.assertEqual(
            _outreach_plan_structure_error(response),
            "every step must contain at least one reply_messages text item",
        )

    def test_message_composition_keeps_two_texts_before_locked_asset_and_card(self) -> None:
        messages = _compose_outreach_messages(
            ["第一条价值信息", "第二条自然承接"],
            resolved_asset={"type": "image", "url": "https://cdn.example/real.jpg"},
            should_send_payment_collection=True,
        )

        self.assertEqual(
            [item["type"] for item in messages],
            ["text", "text", "image", "payment_collection"],
        )
        self.assertEqual([item["order"] for item in messages], [1, 2, 3, 4])

    def test_appointment_message_composition_limits_texts_but_keeps_all_media(self) -> None:
        messages = _compose_outreach_messages(
            ["第一条卡点承接", "第二条卡点价值", "第三条应被裁掉"],
            resolved_assets=[
                {"type": "image", "url": "https://cdn.example/one.jpg"},
                {"type": "image", "url": "https://cdn.example/two.jpg"},
                {"type": "video", "url": "https://cdn.example/three.mp4"},
            ],
        )

        self.assertEqual(
            [item["type"] for item in messages],
            ["text", "text", "image", "image", "video"],
        )
        self.assertEqual([item["order"] for item in messages], [1, 2, 3, 4, 5])

    def test_mainline_sop_materialization_keeps_full_order_and_filters_non_allowed_card(self) -> None:
        pack_messages = [
            {"type": "text", "order": 1, "text": "第一段"},
            {"type": "image", "order": 2, "url": "https://cdn.example/one.jpg"},
            {"type": "image", "order": 3, "url": "https://cdn.example/two.jpg"},
            {"type": "text", "order": 4, "text": "第二段"},
            {"type": "text", "order": 5, "text": "第三段"},
            {"type": "payment_collection", "order": 6, "amount": 10},
        ]

        activity_messages = _first_day_materialized_sop_messages(
            pack_messages,
            allow_payment_collection=True,
        )
        self.assertEqual(
            [item["type"] for item in activity_messages],
            ["text", "image", "image", "text", "text", "payment_collection"],
        )
        self.assertEqual([item["order"] for item in activity_messages], [1, 2, 3, 4, 5, 6])

        non_activity_messages = _first_day_materialized_sop_messages(
            pack_messages,
            allow_payment_collection=False,
        )
        self.assertEqual(
            [item["type"] for item in non_activity_messages],
            ["text", "image", "image", "text", "text"],
        )

        deposit_messages = _first_day_materialized_sop_messages(
            pack_messages,
            allow_payment_collection=False,
            text_overrides=["亲，可以微信转账或发10元红包预约，到店抵扣。"],
        )
        self.assertEqual(
            [item["type"] for item in deposit_messages],
            ["text", "image", "image"],
        )
        self.assertEqual(
            deposit_messages[0]["content"]["text"],
            "亲，可以微信转账或发10元红包预约，到店抵扣。",
        )

    def test_mainline_sop_source_resolves_all_configured_media(self) -> None:
        source_id = "sop-pack:s10_need_and_case"
        catalog = [
            {
                "asset_id": f"{source_id}:2",
                "type": "image",
                "url": "https://cdn.example/one.jpg",
            },
            {
                "asset_id": f"{source_id}:3",
                "type": "image",
                "url": "https://cdn.example/two.jpg",
            },
        ]
        snapshot = {
            "first_day_workflow": {
                "scene_analysis": {
                    "selected_source_ids": {"step1": [source_id], "step2": []},
                }
            },
            "first_day_sop_sequence": [{"source_id": source_id}],
        }

        assets = _first_day_configured_assets_for_step(
            snapshot,
            step_index=1,
            asset_catalog=catalog,
            recent_media={"urls": []},
        )

        self.assertEqual([asset["asset_id"] for asset in assets], [
            f"{source_id}:2",
            f"{source_id}:3",
        ])

    def test_manual_plan_context_derives_reply_wait_from_cached_message_facts(self) -> None:
        now = datetime(2026, 7, 30, 4, 35, tzinfo=timezone.utc)

        activity = _conversation_activity_from_context(
            existing={},
            memory={
                "last_customer_message_at": "2026-07-26T00:50:49+00:00",
                "last_staff_message_at": "2026-07-28T06:01:46+00:00",
            },
            recent_messages=[
                {
                    "from": "customer",
                    "content": "store location",
                    "msgtime": 1785027049766,
                },
                {
                    "from": "staff",
                    "content": "follow-up",
                    "msgtime": 1785218506172,
                },
            ],
            now=now,
        )

        self.assertTrue(activity["awaiting_customer_reply"])
        self.assertEqual(activity["real_customer_message_count"], 1)
        self.assertEqual(activity["reply_wait_minutes"], 2793)
        self.assertEqual(activity["customer_silence_minutes"], 5984)
        self.assertEqual(
            activity["latest_staff_message_at"],
            "2026-07-28T06:01:46+00:00",
        )

    def test_long_silence_requires_a_value_first_step_without_cta(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["persuasion_angle"] = "convenience"
        response["steps"][0]["cta"] = "选择先了解效果还是活动"

        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                reply_wait_minutes=5760,
            ),
            (
                "reply_wait_minutes is at least 1440; rewrite the first step with cta exactly 'none', "
                "persuasion_angle one of education/proof/professionalism/self_image, and a declarative "
                "customer text with no question mark that directly delivers useful value"
            ),
        )

    def test_recent_silence_keeps_model_selected_first_step(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["persuasion_angle"] = "convenience"

        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                reply_wait_minutes=20,
            ),
            "",
        )

    def test_first_day_payment_card_can_use_prior_internal_activity_quote(self) -> None:
        response = {
            "should_create_plan": True,
            "steps": [
                {
                    "scene": "activity_intro",
                    "reply_messages": [
                        {
                            "type": "text",
                            "content": {
                                "text": (
                                    "亲，现在是线上活动价268元，包含淡斑、检测皮肤、基础清洁和肌肤补水。"
                                    "线上预定10元到店抵扣；未做或不满意可退，实际按付款记录核对。"
                                )
                            },
                        }
                    ],
                    "should_send_payment_collection": False,
                },
                {
                    "scene": "deposit_close",
                    "reply_messages": [
                        {"type": "text", "content": {"text": "亲，第二步可以先把活动名额锁住。"}}
                    ],
                    "should_send_payment_collection": True,
                },
            ],
        }

        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                allow_first_day_internal_activity_quote=True,
            ),
            "",
        )
        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                allow_first_day_internal_activity_quote=False,
            ),
            "activity quote is incomplete; payment_collection must be disabled",
        )

    def test_first_day_activity_pack_can_quote_then_send_its_own_card(self) -> None:
        response = {
            "should_create_plan": True,
            "steps": [
                {
                    "scene": "activity_intro",
                    "reply_messages": [
                        {
                            "type": "text",
                            "content": {
                                "text": (
                                    "亲，活动价268元，包含淡斑、检测皮肤、基础清洁和肌肤补水。"
                                    "线上预定10元到店抵扣；未做或不满意可退，实际按付款记录核对。"
                                )
                            },
                        }
                    ],
                    "should_send_payment_collection": True,
                },
                {
                    "scene": "trust_repair",
                    "reply_messages": [
                        {"type": "text", "content": {"text": "到店先了解清楚再决定。"}}
                    ],
                    "should_send_payment_collection": False,
                },
            ],
        }

        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                allow_first_day_internal_activity_quote=True,
            ),
            "",
        )

    def test_long_customer_silence_requires_immediate_first_touch_and_daily_spacing(self) -> None:
        response = _ModelClient().response
        response["steps"][0]["delay_minutes"] = 360

        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                reply_wait_minutes=2793,
                customer_silence_minutes=6048,
            ),
            (
                "customer_silence_minutes is at least 4320; first step delay_minutes "
                "must be between 0 and 180"
            ),
        )

        response["steps"][0]["delay_minutes"] = 60
        response["steps"][1]["delay_minutes"] = 780
        self.assertEqual(
            _outreach_plan_context_error(
                response,
                activity_quote_fact={"completed": False},
                reply_wait_minutes=2793,
                customer_silence_minutes=6048,
            ),
            (
                "customer_silence_minutes is at least 4320; adjacent steps must be "
                "at least 1440 minutes apart"
            ),
        )

    def test_final_step_requires_an_explicit_action(self) -> None:
        response = _ModelClient().response
        response["steps"][-1]["cta"] = "none"

        self.assertEqual(
            _outreach_plan_structure_error(response),
            "final step must contain one explicit customer action",
        )

    def test_single_step_plan_is_rejected_so_the_cycle_cannot_end_after_one_touch(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "只轻触一次获取门店匹配所需区域，客户不回复则停止。",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 720,
                    "timing_reason": "客户意向较低，只安排一次低压力触达",
                    "urgency_level": "normal",
                    "content_mode": "value_only",
                    "persuasion_angle": "convenience",
                    "new_value": "只需提供城市或区域即可匹配真实门店",
                    "reply_messages": [
                        {
                            "type": "text",
                            "order": 1,
                            "content": {
                                "text": "您方便时发我城市或区域就行，我按真实门店帮您看下。"
                            },
                        }
                    ],
                    "asset_strategy": "none",
                    "cta": "提供城市或区域",
                    "payment_collection_basis": "none",
                    "payment_collection_evidence": {
                        "activity_quote_message_index": None
                    },
                    "should_send_payment_collection": False,
                }
            ],
        }

        self.assertEqual(
            _outreach_plan_structure_error(response),
            "plan must contain 2 to 3 steps",
        )

    def test_schedule_supports_immediate_touch_and_daily_limit(self) -> None:
        schedule = _normalize_outreach_schedule(
            "2026-07-28T01:00:00+00:00",
            [
                {"delay_minutes": 0},
                {"delay_minutes": 360},
                {"delay_minutes": 720},
            ],
        )

        self.assertEqual(schedule[0]["scheduled_at"], "2026-07-28T01:00:00+00:00")
        self.assertEqual(schedule[1]["scheduled_at"], "2026-07-28T07:00:00+00:00")
        self.assertEqual(schedule[2]["scheduled_at"], "2026-07-29T00:30:00+00:00")

    def test_schedule_moves_quiet_hour_touch_to_beijing_0830(self) -> None:
        schedule = _normalize_outreach_schedule(
            "2026-07-28T13:30:00+00:00",
            [{"delay_minutes": 60}, {"delay_minutes": 600}],
        )

        self.assertEqual(schedule[0]["scheduled_at"], "2026-07-29T00:30:00+00:00")
        self.assertGreaterEqual(schedule[1]["normalized_delay_minutes"], 60 + 360)

    def test_first_day_schedule_keeps_immediate_and_second_touch_15_to_20_minutes(self) -> None:
        schedule = _normalize_first_day_outreach_schedule(
            "2026-08-06T02:00:00+00:00",
            [
                {"delay_minutes": 0},
                {"delay_minutes": 10, "urgency_level": "immediate"},
            ],
        )

        self.assertEqual(schedule[0]["normalized_delay_minutes"], 0)
        self.assertEqual(schedule[1]["normalized_delay_minutes"], 15)

    def test_first_day_schedule_caps_second_touch_at_20_minutes(self) -> None:
        schedule = _normalize_first_day_outreach_schedule(
            "2026-08-06T02:00:00+00:00",
            [
                {"delay_minutes": 0},
                {"delay_minutes": 120, "urgency_level": "same_day"},
            ],
        )

        self.assertEqual(schedule[1]["normalized_delay_minutes"], 20)

    def test_activity_quote_fact_uses_visible_quote_or_structured_sop_progress(self) -> None:
        message_fact = build_outreach_activity_quote_fact(
            [
                {"direction": "staff", "content": "活动和流程已经介绍过。"},
                {"direction": "staff", "content": "周年庆活动总价268元，每位先付10元预约金。"},
            ],
            {},
        )
        self.assertTrue(message_fact["completed"])
        self.assertEqual(message_fact["message_indexes"], [1])

        progress_fact = build_outreach_activity_quote_fact(
            [{"direction": "staff", "content": "活动和流程已经介绍过。"}],
            {"sop_progress_evidence": {"completed_pack_ids": ["s10_activity_intro"]}},
        )
        self.assertTrue(progress_fact["completed"])
        self.assertEqual(progress_fact["structured_sources"], ["sop_progress"])

    def test_activity_quote_fact_rejects_generic_activity_summary(self) -> None:
        fact = build_outreach_activity_quote_fact(
            [{"direction": "staff", "content": "活动和到店流程已经介绍过。"}],
            {},
        )
        self.assertFalse(fact["completed"])
        self.assertEqual(fact["message_indexes"], [])

    def test_silence_monitor_prefilter_uses_latest_staff_reply(self) -> None:
        base = {
            "sales_contact_started_at": "2000-01-01T00:00:00+08:00",
            "last_customer_message_at": "2026-07-29T09:00:00+08:00",
            "awaiting_customer_reply": True,
        }

        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "reply_wait_minutes": 5},
                silent_minutes=10,
            ),
            "reply_wait_below_threshold",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "reply_wait_minutes": 10},
                silent_minutes=10,
            ),
            "",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "awaiting_customer_reply": False, "reply_wait_minutes": 30},
                silent_minutes=10,
            ),
            "not_waiting_for_customer_reply",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {
                    **base,
                    "sales_contact_started_at": datetime.now(timezone.utc).isoformat(),
                    "reply_wait_minutes": 30,
                },
                silent_minutes=10,
            ),
            "not_proven_day2_plus",
        )

    async def test_silence_monitor_creates_and_activates_one_plan(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=30)).isoformat()
        staff_at = (now - timedelta(minutes=11)).isoformat()
        repository = _Repository()
        repository.candidates = [
            _monitor_candidate(
                customer_at=customer_at,
                staff_at=staff_at,
            )
        ]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我考虑下", "created_at": customer_at},
                {"direction": "staff", "content": "您慢慢考虑", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_silent_customers(
            limit=5,
            silent_minutes=10,
            auto_activate=True,
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(repository.updated_statuses, [("plan-created", "active")])
        self.assertEqual(
            repository.created_plan["source_snapshot"]["trigger_context"]["source"],
            "silence_monitor",
        )
        self.assertEqual(len(model.calls), 2)

    async def test_first_day_opened_silence_monitor_creates_two_step_auto_plan(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        response = _ModelClient().response
        response["steps"][0]["delay_minutes"] = 0
        response["steps"][1]["delay_minutes"] = 15
        response["steps"][1]["urgency_level"] = "immediate"
        response["steps"][0]["scene"] = "store_area_request"
        response["steps"][1]["scene"] = "trust_repair"
        scene_analysis = _first_day_scene_analysis(
            step1_scene="store_area_request",
            step2_scene="trust_repair",
        )
        model = _SequenceModelClient(
            [
                scene_analysis,
                response,
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                },
            ]
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "你好，在吗", "created_at": customer_at},
                {"direction": "staff", "content": "在的亲", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(len(repository.created_plan["tasks"]), 2)
        self.assertEqual(repository.created_plan["sop_plan_id"], "first_day_opened_silence")
        trigger = repository.created_plan["source_snapshot"]["trigger_context"]
        self.assertEqual(trigger["trigger_type"], "first_day_opened_silence")
        self.assertEqual(
            repository.created_plan["tasks"][0]["content_sources"][2]["outreach_task_metadata"][
                "normalized_delay_minutes"
            ],
            0,
        )
        self.assertEqual(
            repository.created_plan["tasks"][1]["content_sources"][2]["outreach_task_metadata"][
                "normalized_delay_minutes"
            ],
            15,
        )
        self.assertEqual(repository.updated_statuses, [("plan-created", "active")])
        workflow = repository.created_plan["source_snapshot"]["first_day_workflow"]
        self.assertEqual(workflow["scene_analysis"]["step1_scene"], "store_area_request")
        self.assertEqual(workflow["verifier_result"]["decision"], "pass")
        self.assertEqual(
            repository.created_plan["source_snapshot"]["personalized_order_gate"]["reason"],
            "still_spoken_without_booked_order",
        )
        self.assertEqual(len(model.calls), 3)

    async def test_first_day_monitor_skips_before_model_after_two_plans_today(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.first_day_plan_count = 2
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我再考虑一下", "created_at": customer_at},
                {"direction": "staff", "content": "好的亲", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["results"][0]["reason"], "first_day_daily_plan_limit_reached")
        self.assertEqual(result["results"][0]["created_today"], 2)
        self.assertEqual(model.calls, [])

    async def test_first_day_monitor_allows_second_plan_today(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.first_day_plan_count = 1
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        response = _ModelClient().response
        response["steps"][0].update({"delay_minutes": 0, "scene": "store_area_request"})
        response["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "trust_repair"}
        )
        model = _SequenceModelClient(
            [
                _first_day_scene_analysis(
                    step1_scene="store_area_request",
                    step2_scene="trust_repair",
                ),
                response,
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                },
            ]
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "武汉哪里有店", "created_at": customer_at},
                {"direction": "staff", "content": "您在武汉哪个区呢？", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(len(model.calls), 3)

    async def test_first_day_monitor_retries_legacy_never_spoke_soft_block_when_customer_now_spoke(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.first_day_run_by_fingerprint = {
            "workflow_run_id": "legacy-soft-block",
            "status": "blocked",
            "reason_code": "customer_never_spoke",
            "final_decision": "no_plan",
            "retry_count": 0,
            "workflow": {},
        }
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        response = _ModelClient().response
        response["steps"][0].update({"delay_minutes": 0, "scene": "activity_intro"})
        response["steps"][1].update({"delay_minutes": 15, "scene": "trust_repair"})
        model = _SequenceModelClient(
            [
                _first_day_scene_analysis(
                    step1_scene="activity_intro",
                    step2_scene="trust_repair",
                ),
                response,
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                },
            ]
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我想看看活动", "created_at": customer_at},
                {"direction": "staff", "content": "活动我给您发一下", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(repository.first_day_run_updates[0]["workflow_run_id"], "legacy-soft-block")
        self.assertEqual(repository.first_day_run_updates[0]["status"], "running")
        self.assertEqual(repository.first_day_run_updates[0]["retry_count"], 1)
        self.assertEqual(
            repository.first_day_run_updates[0]["workflow"]["retry_reason"],
            "soft_block_retry:customer_never_spoke",
        )
        self.assertEqual(len(model.calls), 3)

    async def test_first_day_monitor_keeps_hard_blocked_fingerprint_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.first_day_run_by_fingerprint = {
            "workflow_run_id": "legacy-hard-block",
            "status": "blocked",
            "reason_code": "customer_deleted",
            "final_decision": "no_plan",
        }
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "你好", "created_at": customer_at},
                {"direction": "staff", "content": "在的", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["results"][0]["reason"], "conversation_fingerprint_already_logged")
        self.assertEqual(model.calls, [])

    async def test_first_day_monitor_skipped_existing_runs_do_not_starve_later_candidates(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        skipped_candidates = []
        for index in range(3):
            customer_id = f"already-{index}"
            candidate = {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "customer_id": customer_id,
                "external_userid": f"external-{index}",
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
            skipped_candidates.append(candidate)
            repository.first_day_runs_by_customer[customer_id] = {
                "workflow_run_id": f"logged-{index}",
                "status": "blocked",
                "reason_code": "customer_deleted",
                "final_decision": "no_plan",
            }
        target = {
            **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
            "customer_id": "target-new",
            "external_userid": "external-target",
            "sales_contact_started_at": first_added_at,
            "reply_wait_minutes": 4,
        }
        repository.candidates = [*skipped_candidates, target]
        response = _ModelClient().response
        response["steps"][0].update({"delay_minutes": 0, "scene": "store_area_request"})
        response["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "trust_repair"}
        )
        model = _SequenceModelClient(
            [
                _first_day_scene_analysis(
                    step1_scene="store_area_request",
                    step2_scene="trust_repair",
                ),
                response,
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                },
            ]
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "I will think about it.", "created_at": customer_at},
                {"direction": "staff", "content": "No problem.", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["skipped_count"], 3)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(repository.created_plan["customer_id"], "target-new")

    async def test_first_day_monitor_retries_stale_running_run_without_plan(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.first_day_run_by_fingerprint = {
            "workflow_run_id": "legacy-stale-running",
            "status": "running",
            "reason_code": "preflight_retry",
            "final_decision": "retrying",
            "retry_count": 1,
            "started_at": (now - timedelta(minutes=30)).isoformat(),
            "updated_at": (now - timedelta(minutes=30)).isoformat(),
            "workflow": {},
        }
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        response = _ModelClient().response
        response["steps"][0].update({"delay_minutes": 0, "scene": "effect_proof"})
        response["steps"][1].update({"delay_minutes": 15, "scene": "activity_intro"})
        model = _SequenceModelClient(
            [
                _first_day_scene_analysis(
                    step1_scene="effect_proof",
                    step2_scene="activity_intro",
                ),
                response,
                {
                    "decision": "pass",
                    "block_category": "none",
                    "violations": [],
                    "repair_instructions": [],
                },
            ]
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "效果怎么样", "created_at": customer_at},
                {"direction": "staff", "content": "效果可以的", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(repository.first_day_run_updates[0]["workflow_run_id"], "legacy-stale-running")
        self.assertEqual(repository.first_day_run_updates[0]["retry_count"], 2)
        self.assertEqual(
            repository.first_day_run_updates[0]["workflow"]["retry_reason"],
            "stale_running_retry",
        )

    async def test_first_day_monitor_expands_candidate_scan_window(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=customer_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        service = _MonitorOutreachService(
            repository=repository,
            model_client=_ModelClient(),
            refreshed_messages=[
                {"direction": "customer", "content": "姝︽眽鏈夊簵鍚?", "created_at": customer_at},
                {"direction": "staff", "content": "鎮ㄥ湪鍝釜鍖哄憿", "created_at": staff_at},
            ],
        )

        await service.evaluate_first_day_opened_silence_customers(
            limit=5,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(repository.list_candidate_limits[0], 1000)

    async def test_first_day_monitor_uses_sop_contact_candidates_when_chat_log_is_missing(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        customer_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.sop_contact_candidates = [
            {
                "customer_id": "sop-customer",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-sop",
                "sales_contact_started_at": first_added_at,
                "updated_at": first_added_at,
                "candidate_source": "sop_send_tasks",
            }
        ]
        response = _ModelClient().response
        response["steps"][0]["delay_minutes"] = 0
        response["steps"][1]["delay_minutes"] = 15
        response["steps"][0]["scene"] = "effect_proof"
        response["steps"][1]["scene"] = "activity_intro"
        response["steps"][1]["urgency_level"] = "immediate"
        service = _MonitorOutreachService(
            repository=repository,
            model_client=_SequenceModelClient(
                [
                    _first_day_scene_analysis(
                        step1_scene="effect_proof",
                        step2_scene="activity_intro",
                    ),
                    response,
                    {
                        "decision": "pass",
                        "block_category": "none",
                        "violations": [],
                        "repair_instructions": [],
                    },
                ]
            ),
            refreshed_messages=[
                {"direction": "customer", "content": "effect?", "created_at": customer_at},
                {"direction": "staff", "content": "let me show you", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=3,
            auto_activate=True,
        )

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(repository.created_plan["customer_id"], "sop-customer")
        self.assertEqual(repository.created_plan["external_userid"], "external-sop")

    async def test_first_day_verifier_can_repair_writer_without_changing_locked_scenes(self) -> None:
        scene_analysis = _first_day_scene_analysis(
            step1_scene="store_area_request",
            step2_scene="trust_repair",
        )
        invalid_writer = {"should_create_plan": True, "steps": [{"step": 1}]}
        repaired = _ModelClient().response
        repaired["steps"][0].update({"delay_minutes": 0, "scene": "store_area_request"})
        repaired["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "trust_repair"}
        )
        model = _SequenceModelClient(
            [
                scene_analysis,
                invalid_writer,
                {
                    "decision": "repair",
                    "block_category": "none",
                    "violations": [
                        {
                            "code": "missing_second_step",
                            "field": "candidate_plan.steps",
                            "evidence": "only one incomplete step",
                        }
                    ],
                    "repair_instructions": [
                        {
                            "field": "candidate_plan.steps",
                            "instruction": "补齐第二步并保留锁定场景",
                        }
                    ],
                },
                repaired,
            ]
        )
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
        )

        result = await service.generate_plan(
            customer_id="22000001",
            source_context={
                "memory": {},
                "recent_messages": [
                    {"direction": "customer", "content": "武汉有门店吗"},
                    {"direction": "staff", "content": "有的亲"},
                ],
                "customer_context": {"source": "platform_agent", "orders": []},
                "customer_relation": {"available": True, "status": "active", "is_deleted": False},
            },
            trigger_context={"trigger_type": "first_day_opened_silence"},
            sop_plan_id="first_day_opened_silence",
        )

        self.assertTrue(result["created"])
        workflow = repository.created_plan["source_snapshot"]["first_day_workflow"]
        self.assertIn("exactly 2 steps", workflow["writer_structure_error"])
        self.assertEqual(workflow["verifier_result"]["decision"], "repair")
        self.assertEqual(
            [task["content_sources"][2]["outreach_task_metadata"]["scene"] for task in repository.created_plan["tasks"]],
            ["store_area_request", "trust_repair"],
        )

    async def test_first_day_verifier_block_rejects_plan_and_records_workflow(self) -> None:
        scene_analysis = _first_day_scene_analysis(
            step1_scene="store_area_request",
            step2_scene="trust_repair",
        )
        writer = _ModelClient().response
        writer["steps"][0].update({"delay_minutes": 0, "scene": "store_area_request"})
        writer["steps"][1].update(
            {"delay_minutes": 15, "urgency_level": "immediate", "scene": "trust_repair"}
        )
        model = _SequenceModelClient(
            [
                scene_analysis,
                writer,
                {
                    "decision": "block",
                    "block_category": "source_hard_boundary",
                    "violations": [
                        {"code": "hard_boundary", "field": "source_snapshot", "evidence": "unsafe"}
                    ],
                    "repair_instructions": [],
                },
            ]
        )
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
        )

        result = await service.generate_plan(
            customer_id="22000001",
            source_context={
                "memory": {},
                "recent_messages": [{"direction": "customer", "content": "先等等"}],
                "customer_context": {"source": "platform_agent", "orders": []},
                "customer_relation": {"available": True, "status": "active", "is_deleted": False},
            },
            trigger_context={"trigger_type": "first_day_opened_silence"},
        )

        self.assertFalse(result["created"])
        self.assertEqual(result["ai_result"]["stall_reason"], "hard_boundary")
        event = repository.events[-1]
        self.assertEqual(event["event_type"], "plan_rejected")
        self.assertEqual(event["payload"]["first_day_workflow"]["verifier_result"]["decision"], "block")

    async def test_first_day_monitor_excludes_wecom_auto_opening(self) -> None:
        now = datetime.now(timezone.utc)
        first_added_at = (now - timedelta(hours=1)).isoformat()
        auto_at = (now - timedelta(minutes=20)).isoformat()
        staff_at = (now - timedelta(minutes=4)).isoformat()
        repository = _Repository()
        repository.candidates = [
            {
                **_monitor_candidate(customer_at=auto_at, staff_at=staff_at),
                "sales_contact_started_at": first_added_at,
                "reply_wait_minutes": 4,
            }
        ]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {
                    "direction": "customer",
                    "content": "我已经添加了你，现在我们可以开始聊天了。",
                    "created_at": auto_at,
                },
                {"direction": "staff", "content": "在的亲", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_first_day_opened_silence_customers(limit=5, silent_minutes=3)

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["results"][0]["reason"], "customer_never_spoke")
        self.assertEqual(model.calls, [])

    async def test_silence_monitor_model_rejection_is_idempotent_for_same_conversation(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=40)).isoformat()
        staff_at = (now - timedelta(minutes=20)).isoformat()
        repository = _Repository()
        repository.candidates = [
            _monitor_candidate(
                customer_at=customer_at,
                staff_at=staff_at,
            )
        ]
        model = _ModelClient(
            response={
                "should_create_plan": False,
                "stall_reason": "当前不适合主动触达",
                "customer_psychology": "需要空间",
            }
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "先不用", "created_at": customer_at},
                {"direction": "staff", "content": "好的", "created_at": staff_at},
            ],
        )

        first = await service.evaluate_silent_customers(limit=5, silent_minutes=10)
        calls_after_first_scan = len(model.calls)
        second = await service.evaluate_silent_customers(limit=5, silent_minutes=10)

        self.assertEqual(first["rejected_count"], 1)
        self.assertEqual(second["evaluated_count"], 0)
        self.assertEqual(
            second["results"][0]["reason"],
            "conversation_fingerprint_already_evaluated",
        )
        self.assertEqual(calls_after_first_scan, 2)
        self.assertEqual(len(model.calls), calls_after_first_scan)

    async def test_silence_monitor_does_not_start_a_new_cycle_without_customer_reply(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(days=2)).isoformat()
        staff_at = (now - timedelta(hours=1)).isoformat()
        repository = _Repository()
        repository.completed_plan = {
            "id": "plan-completed",
            "status": "completed",
            "completed_at": (now - timedelta(minutes=30)).isoformat(),
        }
        repository.candidates = [_monitor_candidate(customer_at=customer_at, staff_at=staff_at)]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我考虑下", "created_at": customer_at},
                {"direction": "staff", "content": "给您补一个参考", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_silent_customers(limit=5, silent_minutes=10)

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(
            result["results"][0]["reason"],
            "outreach_cycle_completed_without_new_customer_reply",
        )
        self.assertEqual(model.calls, [])

    async def test_deleted_customer_skips_plan_generation_before_model_call(self) -> None:
        repository = _Repository()
        model = _ModelClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(deleted=True),
        )

        result = await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
        )

        self.assertEqual(result["reason"], "customer_deleted")
        self.assertEqual(model.calls, [])
        self.assertEqual(repository.created_plan, {})
        self.assertIn(
            "plan_skipped_customer_deleted",
            [event["event_type"] for event in repository.events],
        )

    async def test_silence_monitor_skips_deleted_customer_before_model_call(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=30)).isoformat()
        staff_at = (now - timedelta(minutes=11)).isoformat()
        repository = _Repository()
        repository.candidates = [_monitor_candidate(customer_at=customer_at, staff_at=staff_at)]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我再看看", "created_at": customer_at},
                {"direction": "staff", "content": "好的", "created_at": staff_at},
            ],
            deleted=True,
        )

        result = await service.evaluate_silent_customers(limit=5, silent_minutes=10)

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["results"][0]["reason"], "customer_deleted")
        self.assertEqual(model.calls, [])
        self.assertIn(
            "plan_skipped_customer_deleted",
            [event["event_type"] for event in repository.events],
        )

    def test_sop_event_and_silence_monitor_share_contact_lock(self) -> None:
        service = OutreachService(
            repository=_Repository(),
            model_client=_ModelClient(),
            system_client=_ConversationSystemClient(),
        )
        first = service._plan_lock(
            {
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "wechat": "DY258",
                "external_userid": "external-1",
            }
        )
        second = service._plan_lock(
            {
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "wechat": "dy258",
                "external_userid": "external-1",
            }
        )
        self.assertIs(first, second)

    async def test_platform_task_plan_uses_latest_context_and_auto_queues_drafts(self) -> None:
        repository = _Repository()
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "门店太远了，我再考虑下",
                    "created_at": "2026-07-27T10:00:00+08:00",
                },
                {
                    "direction": "staff",
                    "content": "活动价已经给您介绍过了",
                    "created_at": "2026-07-27T10:01:00+08:00",
                },
            ],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-27T10:00:00+08:00",
                "reply_wait_minutes": 20,
                "customer_silence_minutes": 20,
            },
            customer_context={"orders": [], "deposit_state": "unknown"},
            platform_task={
                "event_id": "platform-task-1",
                "messages": [{"type": "text", "content": {"text": "平台统一跟进"}}],
            },
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(len(model.calls), 2)
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(model_input["recent_messages"][0]["content"], "门店太远了，我再考虑下")
        self.assertTrue(model_input["trigger_context"]["platform_task_filtered"])
        self.assertEqual(model_input["trigger_context"]["activation_policy"], "auto_approved")
        self.assertEqual(repository.created_plan["customer_id"], "22000001")
        self.assertEqual(
            repository.created_plan["tasks"][0]["reply_messages"][0]["content"]["text"],
            "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。",
        )
        self.assertTrue(repository.created_plan["tasks"][0]["before_send_check"])
        self.assertTrue(result["auto_approved"])
        self.assertEqual(result["plan"]["status"], "active")
        self.assertEqual(repository.updated_statuses, [("plan-created", "active")])
        self.assertEqual(repository.events[-1]["event_type"], "plan_auto_approved")

    async def test_platform_task_does_not_start_a_new_cycle_without_customer_reply(self) -> None:
        repository = _Repository()
        repository.completed_plan = {
            "id": "plan-completed",
            "status": "completed",
            "completed_at": "2026-07-29T12:00:00+08:00",
        }
        model = _ModelClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
        )

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
                "latest_staff_message_at": "2026-07-29T11:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-after-cycle", "messages": []},
        )

        self.assertEqual(
            result["reason"],
            "outreach_cycle_completed_without_new_customer_reply",
        )
        self.assertEqual(model.calls, [])

    async def test_existing_active_plan_is_reused_without_another_model_call(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-existing",
                "status": "active",
                "created_at": "2026-07-28T10:00:00+08:00",
            },
            "tasks": [{"id": "task-existing"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-2", "messages": []},
        )

        self.assertTrue(result["reused"])
        self.assertFalse(result["created"])
        self.assertEqual(model.calls, [])
        self.assertIn(
            "platform_task_filtered_plan_reused",
            [event["event_type"] for event in repository.events],
        )

    async def test_legacy_review_plan_is_cancelled_and_replaced_by_auto_approved_plan(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-legacy",
                "status": "draft",
                "created_at": "2026-07-28T10:00:00+08:00",
                "source_snapshot": {
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "review_required",
                    }
                },
            },
            "tasks": [{"id": "task-legacy"}],
            "events": [],
        }
        model = _ModelClient()
        # The fixed July 28 conversation becomes long-silent as wall time moves on.
        # Keep this migration test valid under the long-silence plan contract.
        model.response["steps"][0].update(
            {
                "delay_minutes": 60,
                "content_mode": "value_only",
                "persuasion_angle": "education",
                "cta": "none",
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": {"text": "到店会先看斑点情况和皮肤状态，再按实际情况给建议。"},
                    }
                ],
            }
        )
        model.response["steps"][1]["delay_minutes"] = 1500
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-migrate", "messages": []},
        )

        self.assertTrue(result["created"])
        self.assertTrue(result["auto_approved"])
        self.assertEqual(
            repository.updated_statuses,
            [("plan-legacy", "cancelled"), ("plan-created", "active")],
        )
        self.assertIn(
            "legacy_review_plan_cancelled",
            [event["event_type"] for event in repository.events],
        )

    async def test_customer_reply_after_draft_plan_regenerates_from_latest_conversation(self) -> None:
        repository = _Repository()
        now = datetime.now(timezone.utc)
        plan_created_at = (now - timedelta(hours=2)).isoformat()
        customer_replied_at = (now - timedelta(hours=1)).isoformat()
        repository.active_plan = {
            "plan": {
                "id": "plan-old",
                "status": "draft",
                "created_at": plan_created_at,
            },
            "tasks": [{"id": "task-old"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "我现在主要担心到店还要加钱",
                    "created_at": customer_replied_at,
                }
            ],
            conversation_activity={
                "real_customer_message_count": 2,
                "latest_customer_message_at": customer_replied_at,
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-new-reply", "messages": []},
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(
            repository.updated_statuses,
            [("plan-old", "cancelled"), ("plan-created", "active")],
        )
        self.assertIn(
            "platform_task_plan_superseded_by_customer_reply",
            [event["event_type"] for event in repository.events],
        )
        self.assertEqual(len(model.calls), 2)

    async def test_plan_without_reviewable_draft_fails_instead_of_creating_empty_task(self) -> None:
        repository = _Repository()
        model = _ModelClient(response={"should_create_plan": True, "steps": [{"step": 1, "delay_minutes": 30}]})
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        with self.assertRaisesRegex(RuntimeError, "invalid_structure"):
            await service.ensure_platform_task_plan(
                identity={
                    "customer_id": "22000001",
                    "corp_id": "corp-1",
                    "user_id": "7294",
                    "wechat": "DY258",
                    "external_userid": "external-1",
                },
                conversation_messages=[{"direction": "customer", "content": "我考虑一下"}],
                conversation_activity={"real_customer_message_count": 1},
                customer_context={"orders": []},
                platform_task={"event_id": "platform-task-3", "messages": []},
            )

        self.assertEqual(repository.created_plan, {})

    async def test_invalid_first_plan_response_is_repaired_once(self) -> None:
        valid = _ModelClient().response
        model = _SequenceModelClient(
            [
                {
                    "should_create_plan": True,
                    "steps": [
                        {
                            "step": 1,
                            "persuasion_angle": "empathy",
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "只有一步"}}],
                        }
                    ],
                },
                valid,
            ]
        )
        repository = _Repository()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
            source_context={"memory": {}, "recent_messages": []},
        )

        self.assertTrue(result["created"])
        self.assertEqual(len(model.calls), 3)
        self.assertIn("不符合结构合同", model.calls[1]["messages"][-1]["content"])

    async def test_invalid_final_review_is_repaired_once(self) -> None:
        valid = _ModelClient().response
        model = _SequenceModelClient(
            [
                valid,
                {
                    "should_create_plan": True,
                    "steps": [
                        {
                            "step": 1,
                            "persuasion_angle": "unsupported_angle",
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "结构错误"}}],
                        }
                    ],
                },
                valid,
            ]
        )
        repository = _Repository()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
            source_context={"memory": {}, "recent_messages": []},
        )

        self.assertTrue(result["created"])
        self.assertEqual(len(model.calls), 3)
        repair_payload = model.calls[2]["messages"][-1]["content"]
        self.assertIn("structure_error", repair_payload)
        self.assertIn("unsupported_angle", repair_payload)

    async def test_payment_card_can_be_selected_after_quote_with_matching_unpaid_order(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先共情时间压力，再降低付款决策成本",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先降低客户时间压力",
                    "urgency_level": "same_day",
                    "no_reply_action": "advance_to_next_step",
                    "no_reply_strategy": "未回复则换成低风险成交动作，不再追问时间",
                    "content_mode": "value_only",
                    "intent": "time_reassurance",
                    "persuasion_angle": "empathy",
                    "new_value": "到店时间后定",
                    "avoid_repeating": ["完整活动规则"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，您时间没定也不影响，后面按您方便安排就行。"}}],
                    "asset_strategy": "none",
                    "cta": "回复大概方便的时间",
                    "payment_collection_basis": "none",
                    "payment_collection_evidence": {"activity_quote_message_index": None},
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "价值铺垫后再降低付款门槛",
                    "urgency_level": "normal",
                    "no_reply_action": "end_plan",
                    "no_reply_strategy": "仍未回复则结束本周期",
                    "content_mode": "transaction",
                    "intent": "deposit_value",
                    "persuasion_angle": "low_risk_action",
                    "new_value": "先保留活动资格",
                    "avoid_repeating": ["距离顾虑"],
                    "reply_messages": [
                        {
                            "type": "text",
                            "order": 1,
                            "content": {
                                "text": (
                                    "亲，您可以先付10元把活动资格锁住，到店时间后面再定。"
                                    "这10元到店抵扣，未做或不满意可退，实际按付款记录核对。"
                                )
                            },
                        }
                    ],
                    "asset_strategy": "none",
                    "cta": "支付10元预约金",
                    "payment_collection_basis": "model_selected_after_quote",
                    "payment_collection_evidence": {"activity_quote_message_index": 0},
                    "should_send_payment_collection": True,
                },
            ],
        }
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_ModelClient(response=response),
            system_client=_ConversationSystemClient(),
        )

        await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "staff",
                    "content": "活动总价268，每位先交10元预约金。",
                    "created_at": "2026-07-28T09:00:00+08:00",
                },
                {
                    "direction": "customer",
                    "content": "可以，发入口吧。",
                    "created_at": "2026-07-28T09:01:00+08:00",
                },
            ],
            conversation_activity={
                "real_customer_message_count": 1,
                "reply_wait_minutes": 20,
                "customer_silence_minutes": 20,
            },
            customer_context={
                "source": "platform_agent",
                "orders": [
                    {
                        "id": "order-unpaid",
                        "status": "pending",
                        "is_current_order": True,
                        "store_id": "store-101",
                        "prepay_required": 10,
                        "prepay_paid": 0,
                    }
                ],
            },
            platform_task={"event_id": "platform-task-card", "messages": []},
        )

        first_messages = repository.created_plan["tasks"][0]["reply_messages"]
        second_messages = repository.created_plan["tasks"][1]["reply_messages"]
        self.assertEqual([item["type"] for item in first_messages], ["text"])
        self.assertEqual([item["type"] for item in second_messages], ["text", "payment_collection"])

    async def test_payment_card_is_removed_when_evidence_indices_do_not_match_message_parties(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先专业解释，再降低行动门槛",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "用专业信息先解除顾虑",
                    "urgency_level": "same_day",
                    "no_reply_action": "advance_to_next_step",
                    "no_reply_strategy": "未回复则改为低风险活动价值，不再重复检测流程",
                    "content_mode": "value_only",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "professionalism",
                    "new_value": "到店先检测",
                    "avoid_repeating": ["反弹问题原话"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，前面的活动我再帮您接着留意。"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点情况",
                    "payment_collection_basis": "none",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天再提供低风险动作",
                    "urgency_level": "normal",
                    "no_reply_action": "end_plan",
                    "no_reply_strategy": "仍未回复则结束本周期",
                    "content_mode": "transaction",
                    "intent": "deposit_value",
                    "persuasion_angle": "low_risk_action",
                    "new_value": "活动资格可先保留",
                    "avoid_repeating": ["检测流程"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，活动资格可以先保留，到店时间后面再定。"}}],
                    "asset_strategy": "none",
                    "cta": "支付10元预约金",
                    "payment_collection_basis": "model_selected_after_quote",
                    "payment_collection_evidence": {"activity_quote_message_index": 0},
                    "should_send_payment_collection": True,
                },
            ],
        }
        repaired_response = json.loads(json.dumps(response, ensure_ascii=False))
        repaired_response["steps"][1]["content_mode"] = "soft_conversion"
        repaired_response["steps"][1]["payment_collection_basis"] = "none"
        repaired_response["steps"][1]["payment_collection_evidence"] = {
            "activity_quote_message_index": None
        }
        repaired_response["steps"][1]["should_send_payment_collection"] = False
        repaired_response["steps"][1]["reply_messages"][0]["content"]["text"] = (
            "亲，活动资格可以先保留，到店时间后面再定。您还想继续了解活动吗？"
        )
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_SequenceModelClient([response, repaired_response, repaired_response]),
            system_client=_ConversationSystemClient(),
        )

        await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {"direction": "customer", "content": "会不会反弹"},
                {"direction": "staff", "content": "到店先检测"},
            ],
            conversation_activity={
                "real_customer_message_count": 1,
                "reply_wait_minutes": 20,
                "customer_silence_minutes": 20,
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-no-card", "messages": []},
        )

        self.assertEqual(
            [item["type"] for item in repository.created_plan["tasks"][1]["reply_messages"]],
            ["text"],
        )

    async def test_plan_resolves_configured_and_case_assets_without_model_urls(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先用配置素材科普，再用真实案例增强信任",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先提供操作知识",
                    "urgency_level": "same_day",
                    "no_reply_action": "advance_to_next_step",
                    "no_reply_strategy": "未回复则换真实案例建立效果信任",
                    "content_mode": "value_only",
                    "intent": "education",
                    "persuasion_angle": "education",
                    "new_value": "解释操作过程",
                    "avoid_repeating": ["完整报价"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补一个操作过程参考，您看完会更直观。"}}],
                    "asset_strategy": "operation_video",
                    "asset_id": "appointment-blocker:operation_pack:2",
                    "cta": "看完回复感受",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天用真实案例增强信任",
                    "urgency_level": "normal",
                    "no_reply_action": "end_plan",
                    "no_reply_strategy": "仍未回复则结束本周期",
                    "content_mode": "soft_conversion",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "proof",
                    "new_value": "同类斑点参考",
                    "avoid_repeating": ["操作过程"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我再给您看个同类情况参考，您主要担心的是效果对吧？"}}],
                    "asset_strategy": "case_search",
                    "case_query": "晒斑改善案例",
                    "fallback_asset_id": "appointment-blocker:effect_pack:2",
                    "cta": "回复主要顾虑",
                    "should_send_payment_collection": False,
                },
            ],
        }
        repository = _Repository()
        model = _ModelClient(response=response)
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
            precision_qa_playbook_service=_PrecisionQaPlaybookService(),
            coze_client=_CozeClient(),
        )

        await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
            source_context={"recent_messages": [], "memory": {}},
        )

        self.assertEqual(
            [item["type"] for item in repository.created_plan["tasks"][0]["reply_messages"]],
            ["text", "video"],
        )
        self.assertEqual(
            repository.created_plan["tasks"][0]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/operation.mp4",
        )
        self.assertEqual(
            repository.created_plan["tasks"][1]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/kb-case.jpg",
        )
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertNotIn("url", model_input["asset_catalog"][0])

    async def test_message_model_can_only_rewrite_text_and_cannot_replace_locked_asset(self) -> None:
        repository = _Repository()
        model = _ModelClient(
            response={
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "亲，我给您补个真实参考，您看完回我一句就行。"}},
                    {"type": "image", "order": 2, "content": {"url": "https://evil.example/fake.jpg"}},
                    {"type": "payment_collection", "order": 3, "content": {"amount": 40}},
                ]
            }
        )
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())
        task = {
            "customer_id": "22000001",
            "content_sources": [],
            "content_source_metadata": [
                {"should_send_payment_collection": False},
                {
                    "outreach_task_metadata": {
                        "persuasion_angle": "proof",
                        "new_value": "同类案例",
                        "avoid_repeating": ["完整报价"],
                        "cta": "回复主要顾虑",
                    }
                },
                {
                    "resolved_asset": {
                        "asset_id": "appointment-blocker:effect_pack:2",
                        "type": "image",
                        "url": "https://cdn.example/real.jpg",
                        "source": "appointment_blocker_playbook",
                    }
                },
            ],
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "原草稿"}}],
            "should_send_payment_collection": False,
        }

        messages = await service._generate_task_messages(task=task, plan={})

        self.assertEqual(
            messages,
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，我给您补个真实参考，您看完回我一句就行。"},
                },
                {
                    "type": "image",
                    "order": 2,
                    "content": {"url": "https://cdn.example/real.jpg"},
                },
            ],
        )

    async def test_message_model_can_return_two_natural_text_messages(self) -> None:
        repository = _Repository()
        model = _ModelClient(
            response={
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": {"text": "平时日晒多的话，斑点颜色会更容易显出来。"},
                    },
                    {
                        "type": "text",
                        "order": 2,
                        "content": {"text": "日常先把防晒和补水做好，对皮肤状态也更友好。"},
                    },
                ]
            }
        )
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
        )
        task = {
            "customer_id": "22000001",
            "content_sources": [],
            "content_source_metadata": [
                {
                    "outreach_task_metadata": {
                        "content_mode": "value_only",
                        "persuasion_angle": "education",
                        "new_value": "未讲过的防晒护理知识",
                        "avoid_repeating": ["门店地址", "到店检测"],
                        "cta": "none",
                    }
                }
            ],
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "原草稿第一条"}},
                {"type": "text", "order": 2, "content": {"text": "原草稿第二条"}},
            ],
            "should_send_payment_collection": False,
        }

        messages = await service._generate_task_messages(task=task, plan={})

        self.assertEqual([item["type"] for item in messages], ["text", "text"])
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(model_input["task"]["draft_texts"], ["原草稿第一条", "原草稿第二条"])

    async def test_first_day_appointment_task_keeps_all_media_and_blocks_model_card(self) -> None:
        model = _ModelClient(
            response={
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "第一条卡点承接"}},
                    {"type": "text", "order": 2, "content": {"text": "第二条具体价值"}},
                    {"type": "text", "order": 3, "content": {"text": "第三条应被裁掉"}},
                ]
            }
        )
        service = OutreachService(
            repository=_Repository(),
            model_client=model,
            system_client=_ConversationSystemClient(),
        )
        task = {
            "customer_id": "22000001",
            "step_index": 1,
            "content_source_metadata": [
                {
                    "outreach_task_metadata": {
                        "source_kind": "appointment_blocker",
                        "source_id": "appointment-blocker:YYHF-0001",
                    }
                },
                {
                    "resolved_assets": [
                        {"type": "image", "url": "https://cdn.example/one.jpg"},
                        {"type": "image", "url": "https://cdn.example/two.jpg"},
                    ]
                },
            ],
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "原草稿"}},
            ],
            "should_send_payment_collection": True,
        }
        plan = {
            "source_snapshot": {
                "trigger_context": {"trigger_type": "first_day_opened_silence"},
            }
        }

        messages = await service._generate_task_messages(task=task, plan=plan)

        self.assertEqual(
            [message["type"] for message in messages],
            ["text", "text", "image", "image"],
        )

    async def test_first_day_non_activity_sop_task_filters_stale_payment_card(self) -> None:
        service = OutreachService(
            repository=_Repository(),
            model_client=_ModelClient(),
            system_client=_ConversationSystemClient(),
        )
        task = {
            "customer_id": "22000001",
            "step_index": 1,
            "content_source_metadata": [
                {
                    "outreach_task_metadata": {
                        "source_kind": "mainline_sop",
                        "sop_category": "deposit_push",
                        "preserve_sop_pack_messages": True,
                    }
                }
            ],
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "预约金价值说明"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
                {"type": "image", "order": 3, "content": {"url": "https://cdn.example/proof.jpg"}},
            ],
            "should_send_payment_collection": True,
        }
        plan = {
            "source_snapshot": {
                "trigger_context": {"trigger_type": "first_day_opened_silence"},
            }
        }

        messages = await service._generate_task_messages(task=task, plan=plan)

        self.assertEqual([message["type"] for message in messages], ["text", "image"])
        self.assertEqual([message["order"] for message in messages], [1, 2])

    async def test_first_day_rewritten_mainline_sop_keeps_unlimited_texts_and_all_media(self) -> None:
        model = _ModelClient(
            response={
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "第一段主线内容"}},
                    {"type": "text", "order": 2, "content": {"text": "第二段主线内容"}},
                    {"type": "text", "order": 3, "content": {"text": "第三段主线内容"}},
                ]
            }
        )
        service = OutreachService(
            repository=_Repository(),
            model_client=model,
            system_client=_ConversationSystemClient(),
        )
        task = {
            "customer_id": "22000001",
            "step_index": 2,
            "content_source_metadata": [
                {
                    "outreach_task_metadata": {
                        "source_kind": "mainline_sop",
                        "sop_category": "store_prompt",
                        "preserve_sop_pack_messages": False,
                        "sop_pack_rewrite_reason": "first_day_unsupported_store_action",
                    }
                },
                {
                    "resolved_assets": [
                        {"type": "image", "url": "https://cdn.example/one.jpg"},
                        {"type": "image", "url": "https://cdn.example/two.jpg"},
                    ]
                },
            ],
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "原草稿"}},
            ],
            "should_send_payment_collection": True,
        }
        plan = {
            "source_snapshot": {
                "trigger_context": {"trigger_type": "first_day_opened_silence"},
            }
        }

        messages = await service._generate_task_messages(task=task, plan=plan)

        self.assertEqual(
            [message["type"] for message in messages],
            ["text", "text", "text", "image", "image"],
        )

    async def test_case_search_failure_uses_model_selected_configured_fallback(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先补知识，再补效果参考",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先提供护理知识",
                    "urgency_level": "same_day",
                    "no_reply_action": "advance_to_next_step",
                    "no_reply_strategy": "未回复则换真实效果参考，不重复护理知识",
                    "content_mode": "value_only",
                    "intent": "education",
                    "persuasion_angle": "education",
                    "new_value": "简单护理知识",
                    "avoid_repeating": ["价格"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补个简单护理知识，平时防晒也会影响色素状态。"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点时间",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天补充真实效果参考",
                    "urgency_level": "normal",
                    "no_reply_action": "end_plan",
                    "no_reply_strategy": "仍未回复则结束本周期",
                    "content_mode": "soft_conversion",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "proof",
                    "new_value": "效果参考",
                    "avoid_repeating": ["护理知识"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补个同类参考，您看完会更直观。您看完最担心的是效果还是恢复呢？"}}],
                    "asset_strategy": "case_search",
                    "case_query": "晒斑改善案例",
                    "fallback_asset_id": "appointment-blocker:effect_pack:2",
                    "cta": "回复主要顾虑",
                    "should_send_payment_collection": False,
                },
            ],
        }
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_ModelClient(response=response),
            system_client=_ConversationSystemClient(),
            precision_qa_playbook_service=_PrecisionQaPlaybookService(),
            coze_client=_FailingCozeClient(),
        )

        await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
            source_context={"recent_messages": [], "memory": {}},
        )

        self.assertEqual(
            repository.created_plan["tasks"][1]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/fallback.jpg",
        )


def _first_day_scene_analysis(
    *,
    step1_scene: str,
    step2_scene: str,
) -> dict[str, Any]:
    return {
        "eligible": True,
        "suppress_reason": "",
        "hard_boundary": {"active": False, "type": "none", "message_indexes": [], "fact": "无"},
        "precedence_decision": {"row_id": "freeform", "message_indexes": [0], "reason": "测试"},
        "current_scene": step1_scene,
        "scene_completion_matrix": {
            scene: {
                "status": "not_delivered",
                "message_indexes": [],
                "asset_ids": [],
                "summary": "尚未交付",
            }
            for scene in (
                "store_area_request",
                "effect_proof",
                "activity_intro",
                "objection_resolution",
                "deposit_close",
                "trust_repair",
            )
        },
        "delivered_scenes": [],
        "unresolved_customer_need": "需要自然承接并推进",
        "customer_mainline": {
            "latest_customer_main_need": "需要自然承接并推进",
            "silence_barrier": "客户暂时沉默",
            "symptom_role": "无",
            "next_business_action": "执行第一步锁定场景",
        },
        "step1_scene": step1_scene,
        "step2_scene": step2_scene,
        "step1_objective": "立即推进当前场景",
        "step2_objective": "未回复时推进不同场景",
        "forbidden_repetitions": ["上一条客服回复"],
        "writer_context_message_indexes": [0],
        "selected_source_ids": {"step1": [], "step2": []},
        "required_assets": {
            "step1": {"strategy": "none", "asset_id": "", "reason": "无需素材"},
            "step2": {"strategy": "none", "asset_id": "", "reason": "无需素材"},
        },
        "payment_action": {"step": 0, "allowed": False, "reason": "不发卡"},
        "confidence": 0.9,
        "evidence": [],
    }


class _ModelClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "should_create_plan": True,
            "conversion_stage": "P3_STORE_MATCH",
            "stall_reason": "store_unclear",
            "customer_psychology": "距离顾虑",
            "plan_goal": "让客户重新开口并保留活动资格",
            "plan_arc": "先共情距离顾虑，再用专业检测价值推进",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "客户刚结束对话，先低压力承接",
                    "urgency_level": "same_day",
                    "no_reply_action": "advance_to_next_step",
                    "no_reply_strategy": "未回复则换专业流程价值，不再重复距离顾虑",
                    "content_mode": "soft_conversion",
                    "intent": "store_convenience",
                    "persuasion_angle": "empathy",
                    "new_value": "到店时间可以后定",
                    "avoid_repeating": ["门店距离"],
                    "before_send_check": True,
                    "message_goal": "化解距离顾虑",
                    "scene_delivery_check": {
                        "new_value_delivered": "到店时间可以后定",
                        "historical_difference": "不重复门店距离",
                        "objective_match": "通过低压力安排化解顾虑",
                    },
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。"}}],
                    "asset_strategy": "none",
                    "cta": "回复是否愿意继续了解",
                    "should_send_payment_collection": False,
                    "content_sources": ["s10_offer"],
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天补充专业流程价值",
                    "urgency_level": "normal",
                    "no_reply_action": "end_plan",
                    "no_reply_strategy": "仍未回复则结束本周期",
                    "content_mode": "value_only",
                    "intent": "professional_value",
                    "persuasion_angle": "professionalism",
                    "new_value": "到店先检测再决定",
                    "avoid_repeating": ["活动名额"],
                    "before_send_check": True,
                    "message_goal": "用专业流程降低到店顾虑",
                    "scene_delivery_check": {
                        "new_value_delivered": "先检测再决定",
                        "historical_difference": "切换到专业流程价值",
                        "objective_match": "直接说明到店判断流程",
                    },
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，到店会先看斑点情况和适合的方向，合适再决定，您主要是哪类斑点呢？"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点类型",
                    "should_send_payment_collection": False,
                    "content_sources": ["s10_offer"],
                },
            ],
        }
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return dict(self.response)


class _SequenceModelClient(_ModelClient):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(response=responses[0])
        self.responses = responses

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return dict(self.responses[min(len(self.calls) - 1, len(self.responses) - 1)])


class _PrecisionQaPlaybookService:
    def load(self) -> dict[str, Any]:
        return {
            "version": 4,
            "items": [
                {
                    "content_id": "operation_pack",
                    "blocker_type": "操作顾虑",
                    "applicable_scene": "客户不了解操作方式",
                    "reply_messages": [
                        {"type": "text", "content": "操作说明参考"},
                        {"type": "video", "content": "https://cdn.example/operation.mp4"},
                    ],
                },
                {
                    "content_id": "effect_pack",
                    "blocker_type": "效果顾虑",
                    "applicable_scene": "客户担心效果",
                    "reply_messages": [
                        {"type": "text", "content": "效果说明参考"},
                        {"type": "image", "content": "https://cdn.example/fallback.jpg"},
                    ],
                },
            ]
        }


class _CozeClient:
    async def search_kb(self, kb_name: str, query: str) -> Any:
        assert kb_name == "case_studies"
        assert query == "晒斑改善案例"
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    content='<img src="https://cdn.example/kb-case.jpg"> 同类参考',
                    document_id="case-doc-1",
                )
            ]
        )


class _FailingCozeClient:
    async def search_kb(self, _kb_name: str, _query: str) -> Any:
        raise RuntimeError("kb unavailable")


class _Repository:
    def __init__(self) -> None:
        self.active_plan: dict[str, Any] = {}
        self.completed_plan: dict[str, Any] = {}
        self.created_plan: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.updated_statuses: list[tuple[str, str]] = []
        self.candidates: list[dict[str, Any]] = []
        self.evaluated_fingerprints: set[str] = set()
        self.first_day_plan_count = 0
        self.list_candidate_limits: list[int] = []
        self.sop_contact_candidates: list[dict[str, Any]] = []
        self.first_day_run_by_fingerprint: dict[str, Any] = {}
        self.first_day_runs_by_customer: dict[str, dict[str, Any]] = {}
        self.first_day_run_updates: list[dict[str, Any]] = []

    def list_outreach_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_candidate_limits.append(int(kwargs.get("limit") or 0))
        return [dict(item) for item in self.candidates]

    def list_first_day_sop_contact_candidates(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in self.sop_contact_candidates]

    def get_active_outreach_plan_for_customer(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.active_plan)

    def get_latest_completed_outreach_plan_for_customer(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return dict(self.completed_plan)

    def recent_customer_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"memory": {"last_customer_message_at": "2026-07-27T10:00:00+08:00"}}

    def add_outreach_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}
        trigger = payload.get("trigger_context") if isinstance(payload.get("trigger_context"), dict) else {}
        fingerprint = str(trigger.get("conversation_fingerprint") or "")
        if fingerprint:
            self.evaluated_fingerprints.add(fingerprint)
        return {"event_id": f"event-{len(self.events)}"}

    def has_outreach_evaluation_fingerprint(
        self,
        *,
        conversation_fingerprint: str,
        **_kwargs: Any,
    ) -> bool:
        return conversation_fingerprint in self.evaluated_fingerprints

    def count_outreach_plans_for_trigger_between(self, **_kwargs: Any) -> int:
        return self.first_day_plan_count

    def create_first_day_outreach_run(self, **kwargs: Any) -> dict[str, Any]:
        run = {
            "workflow_run_id": "workflow-created",
            "status": "running",
            "retry_count": 0,
            **kwargs,
        }
        self.first_day_run_by_fingerprint = run
        customer_id = str(kwargs.get("customer_id") or "")
        if customer_id:
            self.first_day_runs_by_customer[customer_id] = dict(run)
        return dict(run)

    def find_first_day_outreach_run_by_fingerprint(self, **kwargs: Any) -> dict[str, Any]:
        customer_id = str(kwargs.get("customer_id") or "")
        if customer_id and customer_id in self.first_day_runs_by_customer:
            return dict(self.first_day_runs_by_customer[customer_id])
        return dict(self.first_day_run_by_fingerprint)

    def update_first_day_outreach_run(self, workflow_run_id: str, **changes: Any) -> dict[str, Any]:
        updated = {"workflow_run_id": workflow_run_id, **changes}
        self.first_day_run_updates.append(updated)
        self.first_day_run_by_fingerprint.update(updated)
        return dict(self.first_day_run_by_fingerprint)

    def get_first_day_outreach_run(
        self,
        workflow_run_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if self.first_day_run_by_fingerprint.get("workflow_run_id") == workflow_run_id:
            return dict(self.first_day_run_by_fingerprint)
        return {}

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        self.updated_statuses.append((plan_id, status))
        if status in {"cancelled", "completed"}:
            self.active_plan = {}
        else:
            self.active_plan = {
                "plan": {"id": plan_id, "status": status},
                "tasks": self.created_plan.get("tasks") or [],
                "events": [],
            }
        return dict(self.active_plan) if self.active_plan else {"plan": {"id": plan_id, "status": status}}

    def get_outreach_plan(self, plan_id: str) -> dict[str, Any]:
        if self.active_plan and (self.active_plan.get("plan") or {}).get("id") == plan_id:
            return dict(self.active_plan)
        if plan_id == "plan-created":
            return {
                "plan": {
                    "id": plan_id,
                    "status": "draft",
                    "customer_id": self.created_plan.get("customer_id"),
                },
                "tasks": self.created_plan.get("tasks") or [],
                "events": [],
            }
        return {}

    def create_outreach_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.created_plan = kwargs
        snapshot = kwargs.get("source_snapshot") if isinstance(kwargs.get("source_snapshot"), dict) else {}
        trigger = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
        fingerprint = str(trigger.get("conversation_fingerprint") or "")
        if fingerprint:
            self.evaluated_fingerprints.add(fingerprint)
        return {
            "plan": {"id": "plan-created", "status": "draft"},
            "tasks": kwargs["tasks"],
            "events": [],
        }


def _monitor_candidate(*, customer_at: str, staff_at: str) -> dict[str, Any]:
    return {
        "customer_id": "22000001",
        "corp_id": "corp-1",
        "user_id": "7294",
        "wechat": "DY258",
        "external_userid": "external-1",
        "sales_contact_started_at": "2000-01-01T00:00:00+08:00",
        "last_customer_message_at": customer_at,
        "latest_outbound_message_at": staff_at,
        "reply_wait_minutes": 10,
        "awaiting_customer_reply": True,
        "last_manual_takeover_at": "",
    }


class _ConversationSystemClient:
    def __init__(self, *, deleted: bool = False) -> None:
        self.deleted = deleted

    async def conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "data": {
                "messages": [],
                "customer_relation": {
                    "status": "deleted" if self.deleted else "active",
                    "is_deleted": self.deleted,
                    "deleted_at": "2026-07-29T10:00:00+08:00" if self.deleted else None,
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
            }
        }


class _MonitorOutreachService(OutreachService):
    def __init__(
        self,
        *,
        repository: _Repository,
        model_client: _ModelClient,
        refreshed_messages: list[dict[str, Any]],
        deleted: bool = False,
    ) -> None:
        super().__init__(
            repository=repository,
            model_client=model_client,
            system_client=_ConversationSystemClient(),
        )
        self.refreshed_messages = refreshed_messages
        self.deleted = deleted

    async def refresh_customer_conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "messages": list(self.refreshed_messages),
            "first_added_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "conversation_id": "ww:staff:external",
            "customer_relation": {
                "available": True,
                "status": "deleted" if self.deleted else "active",
                "is_deleted": self.deleted,
                "deleted_at": "2026-07-29T10:00:00+08:00" if self.deleted else "",
                "updated_at": "2026-07-29T10:00:00+08:00",
            },
            "latest_customer_message_at": self._latest_message_time(
                self.refreshed_messages,
                sender="customer",
            ),
            "latest_staff_message_at": self._latest_message_time(
                self.refreshed_messages,
                sender="staff",
            ),
        }

    async def _load_monitor_customer_context(
        self,
        *,
        identity: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        del identity, memory
        return {"source": "platform_agent", "orders": []}


if __name__ == "__main__":
    unittest.main()
