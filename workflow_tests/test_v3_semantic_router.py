from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.prompts.v3_semantic_router import (
    V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT,
    V3_POST_STORE_ROUTER_SYSTEM_PROMPT,
    V3_SCRIPT_SELECTOR_SYSTEM_PROMPT,
    V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT,
    V3_SEMANTIC_ROUTER_SYSTEM_PROMPT,
    build_v3_checkpoint_router_messages,
    build_v3_post_store_router_messages,
    build_v3_sequence_selector_messages,
    _compact_sequence_steps,
)
from app.prompts.reply_synthesizer import (
    PARALLEL_REPLY_SYSTEM_PROMPT,
    build_parallel_reply_messages,
)
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.v3_semantic_router_service import (
    V3SemanticRouterService,
    _expand_sequence_action_queries,
    _filter_script_groups,
    _normalize_semantic_route,
    _sequences_for_checkpoint,
    script_content_candidates,
)
from app.graph.nodes.reply_contract import (
    _v3_available_assets_for_turn,
)


def _shared_context(message: str = "还是太远了") -> dict:
    return {
        "current_message": {
            "message_ref": "current_message",
            "content": message,
            "message_type": "text",
        },
        "conversation": [
            {"message_ref": "conv_001", "role": "assistant", "content": "这家是离您最近的门店。"},
            {"message_ref": "conv_002", "role": "customer", "content": message},
        ],
        "authoritative_facts": {
            "orders_and_payment": {"resolved_payment": {"deposit_state": "required_unpaid"}},
            "sent_messages": {"case_image_delivery": {"total_events": 1}},
        },
        "derived_observations": {},
    }


def test_sequence_index_marks_live_and_silence_steps_without_executing_them() -> None:
    rendered = _compact_sequence_steps(
        [
            {
                "id": "s1",
                "action_code": "empathy",
                "trigger_base": "last_reply",
                "relative_value": 0,
                "relative_unit": "minute",
            },
            {
                "id": "s2",
                "action_code": "case",
                "trigger_base": "last_reply",
                "relative_value": 10,
                "relative_unit": "minute",
            },
            {
                "id": "s3",
                "action_code": "campaign",
                "trigger_base": "add_wecom_day",
                "fixed_time": "20:30",
            },
        ]
    )

    assert rendered == "s1:empathy@now,s2:case@after_10_minute,s3:campaign@at_20:30"


def test_checkpoint_prompt_distinguishes_pressure_boundary_and_self_resolution() -> None:
    assert "当前沟通边界" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "自行结束该请求" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT


def _sequence() -> dict:
    return {
        "id": "18",
        "sequence_name": "距离卡点跟进",
        "checkpoint_code": "distance",
        "description": "距离无法改善时换到效果和活动价值",
        "steps": [
            {"id": "181", "sort_order": 1, "action_code": "empathy", "action_name": "共情引导", "remark": "承接现实成本"},
            {"id": "182", "sort_order": 2, "action_code": "case", "action_name": "效果案例", "remark": "用真实案例证明价值"},
        ],
    }


def test_selected_sequence_queries_only_model_selected_relevant_steps() -> None:
    route = {
        "checkpoint": {"primary_code": "distance"},
        "sequence_match": {"sequence_ids": ["18"], "relevant_step_ids": ["181"]},
        "script_queries": [
            {
                "checkpoint_code": "distance",
                "action_code": "empathy",
                "sequence_id": "18",
                "step_id": "181",
            }
        ],
    }

    expanded = _expand_sequence_action_queries(route, sequences=[_sequence()])

    assert [(item["action_code"], item["step_id"]) for item in expanded["script_queries"]] == [
        ("empathy", "181"),
    ]


def test_selected_sequence_without_relevant_steps_does_not_query_scripts() -> None:
    route = {
        "checkpoint": {"primary_code": "distance"},
        "sequence_match": {"sequence_ids": ["18"], "relevant_step_ids": []},
        "script_queries": [],
    }

    expanded = _expand_sequence_action_queries(route, sequences=[_sequence()])

    assert expanded["script_queries"] == []


def test_knowledge_focus_does_not_duplicate_same_sequence_script_query() -> None:
    route = {
        "checkpoint": {"primary_code": "distance", "primary_type_id": 9, "primary_tag_id": 1},
        "knowledge_focus": {
            "checkpoint_type_id": 9,
            "checkpoint_code": "distance",
            "checkpoint_tag_id": 1,
            "action_code": "empathy",
            "source": "current_friction",
        },
        "sequence_match": {"sequence_ids": ["18"], "relevant_step_ids": ["181"]},
        "script_queries": [],
    }

    expanded = _expand_sequence_action_queries(route, sequences=[_sequence()])

    assert len(expanded["script_queries"]) == 1
    assert expanded["script_queries"][0]["query_source"] == "model_selected_relevant_step"


def test_two_sequences_with_same_retrieval_signature_query_scripts_once() -> None:
    first = _sequence()
    second = {
        **_sequence(),
        "id": "19",
        "steps": [
            {
                **_sequence()["steps"][0],
                "id": "191",
            }
        ],
    }
    route = {
        "checkpoint": {"primary_code": "distance", "primary_type_id": 9, "primary_tag_id": 1},
        "sequence_match": {
            "sequence_ids": ["18", "19"],
            "relevant_step_ids": ["181", "191"],
        },
        "script_queries": [],
    }

    expanded = _expand_sequence_action_queries(route, sequences=[first, second])

    assert len(expanded["script_queries"]) == 1
    assert expanded["script_queries"][0]["sequence_id"] == "18"


class _SemanticClient:
    available = True

    def __init__(self, *, selected_scripts: list[str] | None = None) -> None:
        self.calls = 0
        self.selected_scripts = selected_scripts or []
        self.last_usage = {"model": "deepseek-v4-flash"}

    async def chat_json(self, messages):
        self.calls += 1
        if "轻量语义初筛器" in messages[0]["content"]:
            return {
                "selected_groups": [
                    {
                        "script_id": script_id,
                        "paragraph_no": 1,
                        "evidence_refs": ["current_message"],
                        "reason": "与当前卡点直接相关",
                    }
                    for script_id in self.selected_scripts
                ],
                "reason": "保留直接相关候选",
            }
        if "参考话术检索器" in messages[0]["content"]:
            return {"selected_script_ids": self.selected_scripts, "reason": "互补证据"}
        if "跟进知识检索器" in messages[0]["content"]:
            return {
                "sequence_match": {
                    "sequence_ids": ["18", "unknown"],
                    "relevant_step_ids": ["181", "unknown-step"],
                    "reason": "先承接距离，再换价值维度",
                }
            }
        return {
            "classification_status": "clear",
            "checkpoint": {
                "primary_code": "distance",
                "secondary_code": "",
                "evidence_refs": ["conv_002"],
                "reason": "客户明确认为最近门店仍远",
            },
            "store_query": {
                "required": False,
                "purpose": "none",
                "location_evidence_refs": [],
            },
            "sequence_match": {
                "sequence_ids": ["18", "unknown"],
                "alternative_sequence_ids": [],
                "relevant_step_ids": ["181", "unknown-step"],
                "reason": "先承接距离，再换价值维度",
            },
        }


class _KnowledgeClient:
    available = True

    def __init__(self, script_count: int = 2) -> None:
        self.script_count = script_count
        self.script_queries: list[tuple[str, str]] = []
        self.sequence_calls = 0

    async def query_all_sequences(self):
        self.sequence_calls += 1
        return {"status": "ok", "source": "follow_knowledge_api", "total": 1, "items": [_sequence()]}

    async def query_script_taxonomy(self):
        return {
            "status": "ok",
            "types": [
                {"id": 1, "code": "distance", "name": "距离/便利", "tags": []},
                {"id": 2, "code": "inquiry", "name": "单纯咨询", "tags": []},
            ],
        }

    async def query_all_scripts(
        self,
        *,
        checkpoint_type_id=None,
        checkpoint_tag_id=None,
        checkpoint_code: str,
        action_code: str,
    ):
        del checkpoint_type_id, checkpoint_tag_id
        self.script_queries.append((checkpoint_code, action_code))
        items = [
            {
                "script_code": f"D{index:02d}",
                "script_name": f"距离参考{index}",
                "body_text": "承认路程成本，再用真实价值帮助客户判断。",
                "checkpoint_code": "distance",
                "checkpoint_name": "距离/便利",
                "action_code": "empathy",
                "action_name": "共情引导",
                "content_type": "text",
                "media": {},
            }
            for index in range(1, self.script_count + 1)
        ]
        return {"status": "ok", "source": "follow_knowledge_api", "total": len(items), "items": items}


def test_router_selects_real_sequence_steps_and_queries_matching_scripts() -> None:
    semantic = _SemanticClient(selected_scripts=["D01", "D02"])
    knowledge = _KnowledgeClient(script_count=2)
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=knowledge,
        script_threshold=12,
        max_scripts=6,
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    route = result["semantic_route"]
    assert route["sequence_match"]["sequence_ids"] == ["18"]
    assert route["sequence_match"]["relevant_step_ids"] == ["181"]
    assert route["script_queries"] == [
        {
            "checkpoint_type_id": 1,
            "checkpoint_tag_id": 0,
            "checkpoint_code": "distance",
            "action_code": "empathy",
            "sequence_id": "18",
            "step_id": "181",
            "query_source": "model_selected_relevant_step",
        },
    ]
    assert knowledge.script_queries == [("distance", "empathy")]
    assert result["knowledge_evidence"]["candidate_count"] == 2
    assert result["knowledge_evidence"]["candidates"][0]["sequence_links"][0]["query_source"] == (
        "model_selected_relevant_step"
    )
    selected_sequence = result["knowledge_evidence"]["sequence_candidates"][0]
    assert selected_sequence["selection_reason"] == "先承接距离，再换价值维度"
    assert selected_sequence["description"] == "距离无法改善时换到效果和活动价值"
    assert selected_sequence["steps"][0]["remark"] == "承接现实成本"
    assert "objective" not in selected_sequence["steps"][0]
    assert result["tool_plan"]["decision"] == "facts_sufficient"
    assert semantic.calls == 3


def test_sequence_selector_cannot_overwrite_first_pass_semantics_or_refs() -> None:
    class _OverwritingSelector(_SemanticClient):
        async def chat_json(self, messages):
            self.calls += 1
            return {
                "classification_status": "none",
                "current_intent": {"summary": "被二次改写", "evidence_refs": []},
                "current_friction": {"summary": "被二次改写", "evidence_refs": []},
                "checkpoint": {"primary_code": "", "evidence_refs": []},
                "sequence_match": {
                    "sequence_ids": ["18"],
                    "relevant_step_ids": ["181"],
                    "reason": "选择真实候选",
                },
            }

    service = V3SemanticRouterService(
        semantic_client=_OverwritingSelector(),
        knowledge_client=_KnowledgeClient(),
    )
    first_pass = {
        "classification_status": "clear",
        "current_intent": {"summary": "客户认为门店远", "evidence_refs": ["conv_002"]},
        "current_friction": {
            "checkpoint_type_id": 1,
            "checkpoint_code": "distance",
            "checkpoint_tag_id": 0,
            "summary": "客户明确认为门店远",
            "evidence_refs": ["conv_002"],
            "status": "explicit",
        },
        "historical_unresolved_friction": {"checkpoint_code": "", "summary": "", "evidence_refs": []},
        "knowledge_focus": {
            "checkpoint_type_id": 1,
            "checkpoint_code": "distance",
            "checkpoint_tag_id": 0,
            "action_code": "empathy",
            "source": "current_friction",
            "evidence_refs": ["conv_002"],
            "reason": "first pass retrieval focus",
        },
        "checkpoint": {
            "primary_type_id": 1,
            "primary_code": "distance",
            "primary_tag_id": 0,
            "evidence_refs": ["conv_002"],
        },
        "store_query": {"required": False, "purpose": "none", "location_evidence_refs": []},
    }

    result, _ = asyncio.run(
        service._select_sequence_route(
            shared_context=_shared_context(),
            checkpoint_route=first_pass,
            sequences=[_sequence()],
            checkpoint_taxonomy=[
                {
                    "id": 1,
                    "code": "distance",
                    "name": "距离/便利",
                    "tags": [],
                    "action_counts": {"empathy": 1},
                }
            ],
        )
    )

    assert result["classification_status"] == "clear"
    assert result["current_intent"]["summary"] == "客户认为门店远"
    assert result["current_intent"]["evidence_refs"] == ["current_message", "conv_002"]
    assert result["current_friction"]["summary"] == "客户明确认为门店远"
    assert result["checkpoint"]["primary_code"] == "distance"
    assert result["knowledge_focus"]["action_code"] == "empathy"


def test_checkpoint_router_anchors_current_semantics_without_a_repair_call() -> None:
    class _MissingThenValidRefs(_SemanticClient):
        async def chat_json(self, messages):
            self.calls += 1
            refs = []
            return {
                "classification_status": "clear",
                "current_intent": {"summary": "客户明确认为门店远", "evidence_refs": refs},
                "current_friction": {
                    "checkpoint_type_id": 1,
                    "checkpoint_code": "distance",
                    "checkpoint_tag_id": 0,
                    "summary": "客户明确认为门店远",
                    "evidence_refs": refs,
                    "status": "explicit",
                },
                "historical_unresolved_friction": {"checkpoint_code": "", "summary": "", "evidence_refs": []},
                "checkpoint": {
                    "primary_type_id": 1,
                    "primary_code": "distance",
                    "primary_tag_id": 0,
                    "evidence_refs": refs,
                },
                "sequence_match": {
                    "sequence_ids": ["18"],
                    "relevant_step_ids": ["181"],
                    "reason": "使用真实距离序列",
                },
                "store_query": {"required": False, "purpose": "none", "location_evidence_refs": []},
                "relevant_fact_topic_ids": [],
            }

    semantic = _MissingThenValidRefs()
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=0),
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    assert semantic.calls == 2
    assert result["semantic_route"]["contract_repair_used"] is False
    assert result["semantic_route"]["contract_issues"] == []
    assert result["semantic_route"]["current_intent"]["evidence_refs"] == ["current_message"]
    assert result["semantic_route"]["current_friction"]["evidence_refs"] == ["current_message"]


class _TieredKnowledgeClient(_KnowledgeClient):
    def __init__(self, *, broad_items: list[dict] | None = None) -> None:
        super().__init__(script_count=0)
        self.broad_items = broad_items or []
        self.queries: list[tuple[int, int, str]] = []

    async def query_all_scripts(
        self,
        *,
        checkpoint_type_id=None,
        checkpoint_tag_id=None,
        checkpoint_code: str,
        action_code: str,
    ):
        del checkpoint_code
        type_id = int(checkpoint_type_id or 0)
        tag_id = int(checkpoint_tag_id or 0)
        self.queries.append((type_id, tag_id, action_code))
        items = [] if tag_id else self.broad_items
        return {"status": "ok", "source": "follow_knowledge_api", "total": len(items), "items": items}


def test_script_retrieval_falls_back_only_from_exact_tag_to_same_type_and_action() -> None:
    broad_script = {
        "id": "92",
        "script_code": "KD-0092",
        "script_name": "时间卡点低门槛",
        "body_text": "先不锁具体时间，方便时再衔接。",
        "checkpoint_type": {"id": 18, "code": "cp2", "name": "时间/拖延"},
        "checkpoint_tag": {"id": 61, "name": "到店时间未定"},
        "action_code": "low_barrier",
        "action_name": "低门槛邀请",
        "paragraphs": [],
    }
    knowledge = _TieredKnowledgeClient(broad_items=[broad_script])
    service = V3SemanticRouterService(
        semantic_client=_SemanticClient(),
        knowledge_client=knowledge,
    )
    route = {
        "script_queries": [
            {
                "checkpoint_code": "cp2",
                "checkpoint_type_id": 18,
                "checkpoint_tag_id": 56,
                "action_code": "low_barrier",
                "sequence_id": "13",
                "step_id": "131",
            }
        ]
    }

    result = asyncio.run(service._script_candidates(route))

    assert knowledge.queries == [(18, 56, "low_barrier"), (18, 0, "low_barrier")]
    assert result["support_level"] == "script_broad"
    assert result["items"][0]["retrieval_match_scope"] == "checkpoint_type_action"
    assert result["query_results"][0]["fallback_used"] is True


def test_script_retrieval_keeps_sequence_only_when_selected_action_has_no_script() -> None:
    knowledge = _TieredKnowledgeClient(broad_items=[])
    service = V3SemanticRouterService(
        semantic_client=_SemanticClient(),
        knowledge_client=knowledge,
    )
    route = {
        "script_queries": [
            {
                "checkpoint_code": "price",
                "checkpoint_type_id": 10,
                "checkpoint_tag_id": 16,
                "action_code": "low_barrier",
                "sequence_id": "24",
                "step_id": "241",
            }
        ]
    }

    result = asyncio.run(service._script_candidates(route))

    assert knowledge.queries == [(10, 16, "low_barrier"), (10, 0, "low_barrier")]
    assert result["support_level"] == "sequence_only"
    assert result["items"] == []


def test_script_retrieval_does_not_mask_exact_query_failure_as_empty_inventory() -> None:
    knowledge = _TieredKnowledgeClient(broad_items=[])

    async def failed_query(**kwargs):
        knowledge.queries.append(
            (
                int(kwargs.get("checkpoint_type_id") or 0),
                int(kwargs.get("checkpoint_tag_id") or 0),
                str(kwargs.get("action_code") or ""),
            )
        )
        return {"status": "error", "source": "follow_knowledge_api", "total": 0, "items": []}

    knowledge.query_all_scripts = failed_query
    service = V3SemanticRouterService(
        semantic_client=_SemanticClient(),
        knowledge_client=knowledge,
    )
    route = {
        "script_queries": [
            {
                "checkpoint_code": "distance",
                "checkpoint_type_id": 12,
                "checkpoint_tag_id": 36,
                "action_code": "empathy",
                "sequence_id": "18",
                "step_id": "181",
            }
        ]
    }

    result = asyncio.run(service._script_candidates(route))

    assert knowledge.queries == [(12, 36, "empathy")]
    assert result["query_results"][0]["status"] == "error"
    assert result["query_results"][0]["fallback_used"] is False


def test_reply_renders_sequence_only_as_reasoning_support_not_missing_capability() -> None:
    payload = {
        "evidence": {
            "shared_context": {
                "current_message": {"message_ref": "current_message", "content": "还是有点远"},
                "conversation": [],
                "authoritative_facts": {},
                "rules": {},
            },
            "knowledge_evidence": {
                "support_level": "sequence_only",
                "sequence_candidates": [
                    {
                        "sequence_id": "18",
                        "sequence_name": "距离价值承接",
                        "checkpoint_name": "距离/便利",
                        "description": "距离无法改变时换效果或活动价值",
                        "steps": [
                            {
                                "step_id": "182",
                                "action_code": "case",
                                "action_name": "效果案例",
                                "remark": "直接提供真实效果证据",
                            }
                        ],
                    }
                ],
                "candidates": [],
            },
            "content_candidates": [],
        }
    }

    rendered = build_parallel_reply_messages(payload, json_dumps=lambda value: json.dumps(value, ensure_ascii=False))[1]["content"]

    assert "知识支持：只有跟进序列逻辑，没有匹配到成品话术" in rendered
    assert "距离无法改变时换效果或活动价值" in rendered
    assert "没有话术也要依据完整聊天自主销售" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_router_prompt_does_not_let_script_inventory_override_semantics() -> None:
    assert "话术库存只影响是否能提供成品参考" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不能反向改变卡点、序列或步骤动作" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT


def test_checkpoint_router_only_queries_store_for_an_unresolved_store_task() -> None:
    assert "单纯报告当前位置或行程状态" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "完整聊天中没有实际门店卡或公开地址" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    rendered = build_v3_checkpoint_router_messages(
        shared_context=_shared_context(),
        checkpoint_taxonomy=[],
        sequence_index=[],
    )[1]["content"]
    assert "用【当前状态】中的最近门店卡批次数量核对" in rendered
    assert "最近门店卡批次数量：0；门店ID：无" in rendered
    assert "【已启用跟进序列索引】" not in rendered


def test_reply_static_asset_directory_hides_canned_text_but_keeps_real_media() -> None:
    payload = {
        "evidence": {
            "shared_context": {
                "current_message": {"message_ref": "current_message", "content": "多少钱"},
                "conversation": [],
                "authoritative_facts": {},
                "rules": {},
            },
            "knowledge_evidence": {},
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "name": "活动介绍",
                    "asset_role": "activity_offer",
                    "messages": [
                        {"type": "text", "content": {"text": "这是一段不应常驻注入的成品活动话术"}},
                        {"type": "image", "content": {"url": "https://assets.example/activity.png"}},
                    ],
                    "delivery_observation": {"sent_count": 0},
                }
            ],
        },
        "allowed_selected_content_ids": ["s10_activity_intro"],
    }

    rendered = build_parallel_reply_messages(payload, json_dumps=lambda value: json.dumps(value, ensure_ascii=False))[1]["content"]

    assert "https://assets.example/activity.png" in rendered
    assert "这是一段不应常驻注入的成品活动话术" not in rendered


def test_reply_static_asset_directory_excludes_completed_asset_from_selection() -> None:
    payload = {
        "evidence": {
            "shared_context": {
                "current_message": {"message_ref": "current_message", "content": "\u8001\u4eba\u6591"},
                "conversation": [],
                "authoritative_facts": {},
                "rules": {},
            },
            "knowledge_evidence": {},
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "name": "\u6d3b\u52a8\u4ecb\u7ecd",
                    "asset_role": "activity_offer",
                    "delivery_status": "completed",
                    "messages": [
                        {"type": "image", "content": {"url": "https://assets.example/already-sent.png"}},
                    ],
                }
            ],
        },
        "allowed_selected_content_ids": [],
    }

    rendered = build_parallel_reply_messages(
        payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )[1]["content"]

    assert "https://assets.example/already-sent.png" not in rendered
    assert "content_asset:s10_activity_intro" not in rendered


def test_reply_knowledge_context_forbids_rewriting_exact_script_numbers() -> None:
    payload = {
        "evidence": {
            "shared_context": {
                "current_message": {"message_ref": "current_message", "content": "\u592a\u8fdc\u4e86"},
                "conversation": [],
                "authoritative_facts": {},
                "rules": {},
            },
            "knowledge_evidence": {
                "support_level": "script_exact",
                "candidates": [
                    {
                        "script_id": "171",
                        "source_id": "KD-0171",
                        "script_name": "\u8ddd\u79bb\u5f02\u8bae",
                        "reference_text": "\u5916\u5730\u5ba2\u6237\u5f00\u8f662-3\u5c0f\u65f6\u8fc7\u6765",
                    }
                ],
            },
            "content_candidates": [],
        },
        "allowed_selected_content_ids": [],
    }

    rendered = build_parallel_reply_messages(
        payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )[1]["content"]

    assert "\u7cbe\u786e\u6570\u5b57\u53ea\u80fd\u539f\u6837\u5f15\u7528\u6216\u5220\u53bb\uff0c\u4e0d\u80fd\u6539\u6210\u53e6\u4e00\u4e2a\u6570\u5b57" in rendered


def test_router_always_screens_published_scripts_even_above_threshold() -> None:
    semantic = _SemanticClient(selected_scripts=["D02", "D05", "unknown"])
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=13),
        script_threshold=12,
        max_scripts=6,
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    assert semantic.calls == 4
    assert result["knowledge_evidence"]["script_option_count"] == 13
    assert [item["source_id"] for item in result["knowledge_evidence"]["candidates"]] == ["D02", "D05"]
    selector = result["knowledge_evidence"]["selector"]
    assert selector["prefilter"]["status"] == "ok"
    assert [item["script_id"] for item in selector["prefilter"]["selected_groups"]] == ["D02", "D05"]


def test_router_screens_exact_single_script_and_can_exclude_it() -> None:
    class _ExactSemantic(_SemanticClient):
        async def chat_json(self, messages):
            self.calls += 1
            if "参考话术检索器" in messages[0]["content"]:
                return {
                    "selected_script_ids": [],
                    "group_audits": [
                        {
                            "script_id": "P201",
                            "paragraph_no": 1,
                            "decision": "exclude",
                            "reason_code": "hard_fact_conflict",
                        }
                    ],
                    "reason": "旧价格与当前权威活动价冲突",
                }
            return {
                "classification_status": "none",
                "current_intent": {"summary": "客户询问当前活动价格", "evidence_refs": ["current_message"]},
                "current_friction": {"status": "none"},
                "knowledge_focus": {
                    "checkpoint_type_id": 10,
                    "checkpoint_code": "price",
                    "checkpoint_tag_id": 21,
                    "action_code": "campaign",
                    "source": "current_intent",
                    "evidence_refs": ["current_message"],
                    "reason": "检索价格表达",
                },
                "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
                "store_query": {"required": False, "purpose": "none"},
                "script_queries": [],
            }

    class _ExactKnowledge(_KnowledgeClient):
        async def query_all_sequences(self):
            return {"status": "ok", "source": "follow_knowledge_api", "total": 0, "items": []}

        async def query_script_taxonomy(self):
            return {
                "status": "ok",
                "types": [
                    {
                        "id": 10,
                        "code": "price",
                        "name": "价格/费用",
                        "action_counts": {"campaign": 1},
                        "tags": [{"id": 21, "name": "活动价格", "action_counts": {"campaign": 1}}],
                    }
                ],
            }

        async def query_all_scripts(self, **kwargs):
            del kwargs
            return {
                "status": "ok",
                "source": "follow_knowledge_api",
                "total": 1,
                "items": [
                    {
                        "id": "201",
                        "script_code": "P201",
                        "script_name": "旧价格表达",
                        "checkpoint_type": {"id": 10, "code": "price", "name": "价格/费用"},
                        "checkpoint_tag": {"id": 21, "name": "活动价格"},
                        "checkpoint_code": "price",
                        "action_code": "campaign",
                        "paragraphs": [
                            {
                                "paragraph_no": 1,
                                "source_ref": "follow_script:201:p1",
                                "messages": [{"type": "text", "content": "旧价格199元，先登记长期保留"}],
                            }
                        ],
                    }
                ],
            }

    shared = _shared_context("多少钱")
    shared["rules"] = {
        "AUTHORITATIVE FACTS": {
            "offer": {"new_customer_price": 268, "prepay_amount": 10, "tail_amount": 258},
            "transaction_policy": {},
            "store_address_disclosure_policy": {},
        }
    }
    semantic = _ExactSemantic()
    result = asyncio.run(
        V3SemanticRouterService(
            semantic_client=semantic,
            knowledge_client=_ExactKnowledge(),
            script_threshold=12,
        ).route(shared_context=shared)
    )

    assert semantic.calls == 2
    assert result["knowledge_evidence"]["candidate_count"] == 0
    assert result["knowledge_evidence"]["selector"]["status"] == "empty"
    assert result["knowledge_evidence"]["selector"]["excluded_groups"] == [
        {"script_id": "P201", "paragraph_no": 1, "reason_code": "hard_fact_conflict"}
    ]


def test_router_reuses_parallel_prefetched_sequence_index() -> None:
    semantic = _SemanticClient()
    knowledge = _KnowledgeClient(script_count=2)
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=knowledge,
    )
    sequence_result = {
        "status": "ok",
        "source": "follow_knowledge_api",
        "total": 1,
        "items": [_sequence()],
    }

    result = asyncio.run(
        service.route(
            shared_context=_shared_context(),
            sequence_result=sequence_result,
        )
    )

    assert result["knowledge_evidence"]["sequence_index_total"] == 1
    assert knowledge.sequence_calls == 0


class _TwoPhaseStoreSemantic(_SemanticClient):
    async def chat_json(self, messages):
        self.calls += 1
        if "参考话术检索器" in messages[0]["content"]:
            return {"selected_script_ids": ["D01", "D02"], "reason": "保留贴合候选"}
        if "轻量语义路由器" in messages[0]["content"]:
            return {
                "classification_status": "clear",
                "checkpoint": {
                    "primary_code": "distance",
                    "evidence_refs": ["current_message"],
                    "reason": "客户给出新地点并明确嫌远",
                },
                "store_query": {
                    "required": True,
                    "purpose": "store_search",
                    "location_evidence_refs": ["current_message"],
                    "destination_hint": "柳州",
                },
            }
        return {
            "sequence_match": {
                "sequence_ids": ["18"],
                "relevant_step_ids": ["182"],
                "reason": "查询后切换效果价值",
            },
            "store_result_interpretation": {
                "resolved_current_request": True,
                "remaining_customer_concern_refs": ["current_message", "unknown"],
                "reason": "同一目的地没有更近候选",
            },
        }


def test_store_route_defers_sequences_and_scripts_until_store_fact_exists() -> None:
    semantic = _TwoPhaseStoreSemantic()
    knowledge = _KnowledgeClient(script_count=2)
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=knowledge,
    )
    sequence_result = {"status": "ok", "total": 1, "items": [_sequence()]}

    pre = asyncio.run(
        service.route(
            shared_context=_shared_context("柳州这家太远了"),
            sequence_result=sequence_result,
        )
    )

    assert pre["semantic_route"]["phase"] == "pre_store_pending"
    assert pre["semantic_route"]["checkpoint"]["primary_code"] == ""
    assert pre["semantic_route"]["provisional_checkpoint"]["primary_code"] == "distance"
    assert pre["semantic_route"]["sequence_match"]["sequence_ids"] == []
    assert pre["semantic_route"]["script_queries"] == []
    assert pre["knowledge_evidence"]["status"] == "deferred_until_store_resolution"
    assert knowledge.script_queries == []

    final = asyncio.run(
        service.route_after_store(
            shared_context=_shared_context(),
            pre_route=pre["semantic_route"],
            store_resolution_fact={
                "status": "send_single",
                "candidate_search_complete": True,
                "recommendation_final_for_destination": True,
                "delivery_store_ids": ["241"],
            },
            sequence_result=sequence_result,
        )
    )

    route = final["semantic_route"]
    assert route["phase"] == "post_store_final"
    assert route["checkpoint"]["primary_code"] == "distance"
    assert route["store_query"]["required"] is False
    assert route["store_result_interpretation"]["remaining_customer_concern_refs"] == ["current_message"]
    assert knowledge.script_queries == [("distance", "case")]
    assert final["knowledge_evidence"]["candidate_count"] == 2
    assert semantic.calls == 3


class _InquiryStoreSemantic(_SemanticClient):
    async def chat_json(self, messages):
        self.calls += 1
        if "轻量语义路由器" in messages[0]["content"]:
            return {
                "classification_status": "clear",
                "checkpoint": {
                    "primary_code": "inquiry",
                    "evidence_refs": ["current_message"],
                    "reason": "客户只询问柳州门店",
                },
                "store_query": {
                    "required": True,
                    "purpose": "store_search",
                    "location_evidence_refs": ["current_message"],
                    "destination_hint": "柳州",
                },
            }
        return {
            "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
            "store_result_interpretation": {
                "resolved_current_request": False,
                "remaining_customer_concern_refs": ["current_message"],
                "reason": "查询不完整",
            },
        }


def test_post_store_fact_does_not_turn_plain_store_inquiry_into_distance() -> None:
    semantic = _InquiryStoreSemantic()
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=2),
    )
    sequence_result = {"status": "ok", "total": 1, "items": [_sequence()]}
    shared = _shared_context("柳州有店吗")

    pre = asyncio.run(service.route(shared_context=shared, sequence_result=sequence_result))
    final = asyncio.run(
        service.route_after_store(
            shared_context=shared,
            pre_route=pre["semantic_route"],
            store_resolution_fact={"status": "search_incomplete", "candidate_search_complete": False},
            sequence_result=sequence_result,
        )
    )

    assert final["semantic_route"]["checkpoint"]["primary_code"] == "inquiry"
    assert final["semantic_route"]["sequence_match"]["sequence_ids"] == []
    assert final["semantic_route"]["script_queries"] == []
    assert final["semantic_route"]["store_result_interpretation"]["resolved_current_request"] is False


class _PostStoreFactTopicSemantic(_SemanticClient):
    async def chat_json(self, messages):
        self.calls += 1
        return {
            "relevant_fact_topic_ids": ["store_policy"],
            "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
            "store_result_interpretation": {
                "resolved_current_request": False,
                "remaining_customer_concern_refs": ["current_message"],
                "reason": "location still required",
            },
        }


def test_post_store_selector_preserves_first_pass_fact_topics() -> None:
    semantic = _PostStoreFactTopicSemantic()
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=0),
    )
    checkpoint_route = {
        "classification_status": "none",
        "current_intent": {
            "summary": "customer asks about effect frequency and store location",
            "evidence_refs": ["current_message"],
        },
        "current_friction": {"status": "none"},
        "historical_unresolved_friction": {},
        "knowledge_focus": {"source": "none"},
        "checkpoint": {},
        "store_query": {"required": True},
        "relevant_fact_topic_ids": ["effect_evidence"],
    }

    route, _ = asyncio.run(
        service._select_sequence_route(
            shared_context=_shared_context(),
            checkpoint_route=checkpoint_route,
            sequences=[],
            store_resolution_fact={"status": "need_location_confirmation"},
            checkpoint_taxonomy=[],
            fact_topic_catalog=[
                {"id": "effect_evidence"},
                {"id": "store_policy"},
            ],
        )
    )

    assert route["relevant_fact_topic_ids"] == ["effect_evidence", "store_policy"]


def test_non_store_route_uses_checkpoint_then_filtered_sequence_selection() -> None:
    semantic = _SemanticClient(selected_scripts=["D01", "D02"])
    knowledge = _KnowledgeClient(script_count=2)
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=knowledge,
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    assert result["semantic_route"]["phase"] == "non_store_final"
    assert semantic.calls == 3
    assert result["knowledge_evidence"]["candidate_count"] == 2


def test_broad_type_script_query_uses_semantic_selector_below_count_threshold() -> None:
    semantic = _SemanticClient(selected_scripts=["D01"])
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=2),
        script_threshold=12,
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    assert semantic.calls == 3
    assert result["knowledge_evidence"]["script_query_results"][0]["match_scope"] == "checkpoint_type_action"
    assert result["knowledge_evidence"]["support_level"] == "script_broad"
    assert [item["source_id"] for item in result["knowledge_evidence"]["candidates"]] == ["D01"]


def test_paragraph_script_id_cannot_bypass_paragraph_selection() -> None:
    candidates = [
        {
            "script_code": "KD-0092",
            "paragraphs": [
                {"paragraph_no": 1, "messages": [{"type": "text", "content": "旧活动话术"}]}
            ],
        },
        {"script_code": "LEGACY-1", "body_text": "没有段落结构的兼容话术"},
    ]

    result = _filter_script_groups(
        candidates,
        selected_groups=[],
        selected_script_ids=["KD-0092", "LEGACY-1"],
        max_groups=4,
    )

    assert [item["script_code"] for item in result] == ["LEGACY-1"]


def test_paragraph_selection_requires_auditable_model_fields() -> None:
    class _MissingAuditSemantic(_SemanticClient):
        async def chat_json(self, messages):
            del messages
            return {
                "selected_groups": [{"script_id": "P201", "paragraph_no": 1}],
                "selected_script_ids": ["P201"],
                "reason": "缺少审计字段",
            }

    service = V3SemanticRouterService(
        semantic_client=_MissingAuditSemantic(),
        knowledge_client=_KnowledgeClient(),
    )
    selector, narrowed = asyncio.run(
        service._narrow_scripts(
            shared_context=_shared_context("多少钱"),
            semantic_route={"checkpoint": {"primary_code": "price"}},
            candidates=[
                {
                    "script_code": "P201",
                    "paragraphs": [
                        {
                            "paragraph_no": 1,
                            "messages": [{"type": "text", "content": "旧价格199元"}],
                        }
                    ],
                }
            ],
        )
    )

    assert narrowed == []
    assert selector["status"] == "empty"
    assert selector["selected_groups"] == []


def test_plain_price_inquiry_queries_published_script_without_sequence() -> None:
    class _PriceInquirySemantic(_SemanticClient):
        async def chat_json(self, messages):
            self.calls += 1
            if "参考话术检索器" in messages[0]["content"]:
                return {
                    "group_audits": [
                        {
                            "script_id": "P201",
                            "paragraph_no": 1,
                            "decision": "select",
                            "reason_code": "selected",
                            "evidence_refs": ["current_message"],
                            "authority_status": "pass",
                            "action_fit": "direct",
                            "reason": "当前价格事实与客户问题直接对应",
                        }
                    ],
                    "selected_script_ids": ["P201"],
                    "reason": "与当前价格咨询直接相关且不冲突",
                }
            return {
                "classification_status": "none",
                "current_intent": {
                    "summary": "客户询问当前活动价格",
                    "evidence_refs": ["current_message"],
                },
                "current_friction": {"status": "none"},
                "knowledge_focus": {
                    "checkpoint_type_id": 10,
                    "checkpoint_code": "price",
                    "checkpoint_tag_id": 21,
                    "action_code": "campaign",
                    "source": "current_intent",
                    "evidence_refs": ["current_message"],
                    "reason": "活动价格话术能辅助回答并自然推进",
                },
                "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
                "store_query": {"required": False, "purpose": "none"},
                "script_queries": [],
            }

    class _PriceKnowledge(_KnowledgeClient):
        async def query_all_sequences(self):
            return {"status": "ok", "source": "follow_knowledge_api", "total": 0, "items": []}

        async def query_script_taxonomy(self):
            return {
                "status": "ok",
                "types": [
                    {
                        "id": 10,
                        "code": "price",
                        "name": "价格/费用",
                        "action_counts": {"campaign": 3},
                        "tags": [
                            {
                                "id": 21,
                                "name": "活动内容介绍+抢购方式说明",
                                "action_counts": {"campaign": 3},
                            }
                        ],
                    }
                ],
            }

        async def query_all_scripts(
            self,
            *,
            checkpoint_type_id=None,
            checkpoint_tag_id=None,
            checkpoint_code: str,
            action_code: str,
        ):
            self.script_queries.append((checkpoint_code, action_code))
            return {
                "status": "ok",
                "source": "follow_knowledge_api",
                "total": 1,
                "items": [
                    {
                        "id": "201",
                        "script_code": "P201",
                        "script_name": "活动价格说明",
                        "body_text": "活动价和包含内容的业务参考表达",
                        "checkpoint_type": {"id": 10, "code": "price", "name": "价格/费用"},
                        "checkpoint_tag": {"id": 21, "name": "活动内容介绍+抢购方式说明"},
                        "checkpoint_code": "price",
                        "checkpoint_name": "价格/费用",
                        "action_code": "campaign",
                        "action_name": "活动邀约",
                        "paragraphs": [
                            {
                                "paragraph_no": 1,
                                "source_ref": "follow_script:201:p1",
                                "messages": [{"type": "text", "content": "活动价和包含内容的业务参考表达"}],
                            }
                        ],
                        "media": {},
                    }
                ],
            }

    semantic = _PriceInquirySemantic()
    knowledge = _PriceKnowledge()
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=knowledge,
    )

    result = asyncio.run(service.route(shared_context=_shared_context("多少钱")))

    assert result["semantic_route"]["current_friction"]["status"] == "none"
    assert result["semantic_route"]["sequence_match"]["sequence_ids"] == []
    assert result["semantic_route"]["knowledge_focus"]["checkpoint_code"] == "price"
    assert knowledge.script_queries == [("price", "campaign")]
    assert result["knowledge_evidence"]["candidate_count"] == 1
    assert semantic.calls == 2


def test_semantic_prompts_do_not_delegate_customer_reply_or_close_decision() -> None:
    assert "不写客户话术，不决定成交、付款、暂停或最终动作" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "current_friction 只记录当前消息明确表达" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "id>0` 的项目来自已发布话术" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "historical_unresolved_friction" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "不能覆盖当前意图" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "客户没有继续追问不等于该顾虑已经解决" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "客户未再追问不能单独证明旧顾虑已经解决" in V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT
    assert "不得自动提名其后续步骤" in V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT
    assert "最多选择 3 项回答当前问题真正需要的事实" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "即使没有说明原因也不能判为 none" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "不等于客户正在表达距离或价格卡点" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "至少选择 complaint_refund" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "如何参加、报名、支付或明确要继续办理" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "relevant_fact_topic_ids 是必填检索结果" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "只有当前问题完全不需要目录中的额外事实时才允许 []" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "本阶段不选择跟进序列或步骤" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "摘要与类型或标签冲突" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "事实可答性自检" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "当前消息对紧邻问题的明确省略或指代" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "才可再选择最多一个直接相关主题" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "summary 非空，就必须至少引用一条" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "禁止输出“有摘要但 evidence_refs 为空”的结果" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "“费用怎么算、多少钱、包含什么”是在了解事实" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "是否全部能去掉、是否一次完成、会不会反弹" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "未来条件式到店意向本身不是时间阻力" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "不等于禁止继续沟通" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "knowledge_focus 是独立的话术检索焦点，不等于客户有异议" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "不能反向把普通咨询改写成卡点" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "没有合适知识就留空" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "门店查询本身通常是 inquiry，不能猜成 distance" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "序列与话术查询留空，等待门店事实后再选" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "`after_*` 和 `at_*` 只属于后续沉默触达计划" in V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT
    assert "你不生成客户话术，不决定 Reply 最终采用哪个动作" in V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT
    checkpoint_rendered = build_v3_checkpoint_router_messages(
        shared_context=_shared_context(),
        checkpoint_taxonomy=[{"id": 1, "code": "distance", "name": "距离/便利", "tags": []}],
        sequence_index=[_sequence()],
    )
    assert "【完整聊天】" in checkpoint_rendered[1]["content"]
    assert "【已启用跟进序列索引】" not in checkpoint_rendered[1]["content"]
    assert "181:empathy@now" not in checkpoint_rendered[1]["content"]
    assert "182:case@now" not in checkpoint_rendered[1]["content"]
    assert "【租户已发布卡点类型与标签】" in checkpoint_rendered[1]["content"]
    assert "【可选权威事实主题】" in checkpoint_rendered[1]["content"]
    sequence_rendered = build_v3_sequence_selector_messages(
        shared_context=_shared_context(),
        checkpoint_route={"checkpoint": {"primary_code": "distance", "reason": "客户明确嫌远"}},
        sequence_candidates=[_sequence()],
        store_resolution_fact={"status": "send_single", "delivery_store_ids": ["241"]},
    )
    assert "【已启用跟进序列索引】" in sequence_rendered[1]["content"]
    assert "send_single" in sequence_rendered[1]["content"]
    assert "不生成客户话术，不决定成交动作" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "不得超过给定段落上限" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "同一结论只是换措辞不算互补" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "已发布话术是业务批准的销售表达" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "一般性客户经验、社会证明、价值类比" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "必须排除整个 paragraph" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "不能指望 Reply 从污染内容里自行摘取" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "excluded_groups" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "group_audits" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "不再重复输出 selected_groups/excluded_groups" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "不能单独证明客户存在距离顾虑" in V3_POST_STORE_ROUTER_SYSTEM_PROMPT
    assert "store_query.required` 必须为 false" in V3_POST_STORE_ROUTER_SYSTEM_PROMPT
    post_rendered = build_v3_post_store_router_messages(
        shared_context=_shared_context(),
        sequence_index=[_sequence()],
        pre_route={
            "provisional_checkpoint": {"primary_code": "inquiry"},
            "store_query": {"purpose": "store_search", "destination_hint": "柳州"},
        },
        store_resolution_fact={
            "status": "no_valid_candidate",
            "candidate_search_complete": True,
        },
    )
    assert "本轮权威门店查询结果" in post_rendered[1]["content"]
    assert "no_valid_candidate" in post_rendered[1]["content"]


def test_router_normalizes_evaluation_only_audit_fields_against_real_ids() -> None:
    raw = {
        "classification_status": "ambiguous",
        "checkpoint": {
            "primary_code": "distance",
            "secondary_code": "hesitation",
            "evidence_refs": ["conv_002"],
        },
        "sequence_match": {
            "sequence_ids": ["18", "19", "unknown"],
            "alternative_sequence_ids": ["19", "18", "unknown"],
            "relevant_step_ids": ["181"],
            "excluded_sequence_ids": ["20", "18", "unknown"],
            "exclusion_reasons": {"20": "客户没有时间冲突"},
        },
    }
    sequences = [
        _sequence(),
        {"id": "19", "checkpoint_code": "distance", "steps": []},
        {"id": "20", "checkpoint_code": "time_conflict", "steps": []},
    ]

    result = _normalize_semantic_route(raw, shared_context=_shared_context(), sequences=sequences)

    assert result["classification_status"] == "ambiguous"
    assert result["sequence_match"]["sequence_ids"] == ["18", "19"]
    assert result["sequence_match"]["alternative_sequence_ids"] == ["19"]
    assert result["sequence_match"]["excluded_sequence_ids"] == ["20"]
    assert result["sequence_match"]["exclusion_reasons"] == {"20": "客户没有时间冲突"}


def test_router_separates_current_and_historical_friction_and_limits_evidence() -> None:
    sequences = [
        {
            "id": "18",
            "checkpoint_code": "distance",
            "steps": [
                {"id": "181", "action_code": "empathy"},
                {"id": "182", "action_code": "case"},
                {"id": "183", "action_code": "value_add"},
            ],
        },
        {"id": "19", "checkpoint_code": "distance", "steps": [{"id": "191", "action_code": "campaign"}]},
        {"id": "20", "checkpoint_code": "distance", "steps": [{"id": "201", "action_code": "resolve"}]},
    ]
    raw = {
        "classification_status": "clear",
        "current_intent": {
            "summary": "客户认为当前门店太远",
            "evidence_refs": ["conv_001", "conv_002", "missing"],
        },
        "current_friction": {
            "checkpoint_type_id": 12,
            "checkpoint_code": "distance",
            "checkpoint_tag_id": 36,
            "summary": "客户当前明确嫌远",
            "evidence_refs": ["conv_001", "conv_002", "missing"],
            "status": "explicit",
        },
        "historical_unresolved_friction": {
            "checkpoint_code": "price",
            "summary": "此前还担心费用",
            "evidence_refs": ["conv_001", "conv_002"],
        },
        "relevant_fact_topic_ids": [
            "effect_evidence",
            "activity_offer",
            "transport_policy",
            "fee_transparency",
            "unknown",
        ],
        "sequence_match": {
            "sequence_ids": ["18", "19", "20"],
            "relevant_step_ids": ["181", "182", "183", "191", "201"],
        },
    }
    taxonomy = [
        {"id": 12, "code": "distance", "name": "距离/便利", "tags": [{"id": 36, "name": "太远"}]},
        {"id": 13, "code": "price", "name": "价格/费用", "tags": []},
    ]

    result = _normalize_semantic_route(
        raw,
        shared_context=_shared_context(),
        sequences=sequences,
        checkpoint_taxonomy=taxonomy,
        fact_topic_catalog=[
            {"id": "effect_evidence"},
            {"id": "activity_offer"},
            {"id": "transport_policy"},
            {"id": "fee_transparency"},
        ],
    )

    assert result["current_friction"]["checkpoint_code"] == "distance"
    assert result["current_friction"]["checkpoint_type_name"] == "距离/便利"
    assert result["current_friction"]["checkpoint_tag_name"] == "太远"
    assert result["current_friction"]["evidence_refs"] == ["current_message", "conv_002"]
    assert result["current_intent"]["evidence_refs"] == ["current_message", "conv_002"]
    assert result["historical_unresolved_friction"] == {
        "checkpoint_code": "price",
        "summary": "此前还担心费用",
        "evidence_refs": ["conv_002"],
    }
    assert result["relevant_fact_topic_ids"] == [
        "effect_evidence",
        "activity_offer",
        "transport_policy",
    ]
    assert result["sequence_match"]["sequence_ids"] == ["18", "19"]
    assert result["sequence_match"]["relevant_step_ids"] == ["181", "182", "191"]


def test_router_none_friction_does_not_reopen_current_checkpoint() -> None:
    result = _normalize_semantic_route(
        {
            "classification_status": "clear",
            "current_intent": {"summary": "客户只是确认收到", "evidence_refs": ["conv_002"]},
            "current_friction": {
                "checkpoint_type_id": 12,
                "checkpoint_code": "distance",
                "checkpoint_tag_id": 36,
                "summary": "",
                "evidence_refs": ["conv_002"],
                "status": "none",
            },
            "historical_unresolved_friction": {
                "checkpoint_code": "distance",
                "summary": "此前曾嫌远",
                "evidence_refs": ["conv_002"],
            },
            "sequence_match": {"sequence_ids": ["18"], "relevant_step_ids": ["181"]},
        },
        shared_context=_shared_context("好，知道了"),
        sequences=[_sequence()],
        checkpoint_taxonomy=[
            {"id": 12, "code": "distance", "name": "距离/便利", "tags": [{"id": 36, "name": "太远"}]}
        ],
    )

    assert result["classification_status"] == "none"
    assert result["current_friction"]["status"] == "none"
    assert result["checkpoint"]["primary_code"] == ""
    assert result["sequence_match"]["sequence_ids"] == []


def test_plain_inquiry_can_retrieve_scripts_without_becoming_friction() -> None:
    taxonomy = [
        {
            "id": 10,
            "code": "price",
            "name": "价格/费用",
            "action_counts": {"campaign": 3, "resolve": 20},
            "tags": [
                {
                    "id": 21,
                    "name": "活动内容介绍+抢购方式说明",
                    "action_counts": {"campaign": 3},
                }
            ],
        }
    ]
    result = _normalize_semantic_route(
        {
            "classification_status": "none",
            "current_intent": {"summary": "客户询问价格", "evidence_refs": ["current_message"]},
            "current_friction": {"status": "none"},
            "knowledge_focus": {
                "checkpoint_type_id": 10,
                "checkpoint_code": "price",
                "checkpoint_tag_id": 21,
                "action_code": "campaign",
                "source": "current_intent",
                "evidence_refs": ["current_message"],
                "reason": "用已发布活动表达辅助准确回答价格",
            },
        },
        shared_context=_shared_context("多少钱"),
        sequences=[],
        checkpoint_taxonomy=taxonomy,
    )
    result = _expand_sequence_action_queries(result, sequences=[])

    assert result["current_friction"]["status"] == "none"
    assert result["checkpoint"]["primary_code"] == ""
    assert result["sequence_match"]["sequence_ids"] == []
    assert result["knowledge_focus"] == {
        "checkpoint_type_id": 10,
        "checkpoint_code": "price",
        "checkpoint_type_name": "价格/费用",
        "checkpoint_tag_id": 21,
        "checkpoint_tag_name": "活动内容介绍+抢购方式说明",
        "action_code": "campaign",
        "source": "current_intent",
        "evidence_refs": ["current_message"],
        "reason": "用已发布活动表达辅助准确回答价格",
    }
    assert result["script_queries"] == [
        {
            "checkpoint_type_id": 10,
            "checkpoint_tag_id": 21,
            "checkpoint_code": "price",
            "action_code": "campaign",
            "sequence_id": "",
            "step_id": "",
            "query_source": "model_selected_knowledge_focus",
        }
    ]


def test_direct_knowledge_focus_rejects_action_not_published_for_selected_tag() -> None:
    result = _normalize_semantic_route(
        {
            "current_intent": {"summary": "客户询问价格", "evidence_refs": ["current_message"]},
            "current_friction": {"status": "none"},
            "knowledge_focus": {
                "checkpoint_type_id": 10,
                "checkpoint_code": "price",
                "checkpoint_tag_id": 21,
                "action_code": "case",
                "source": "current_intent",
                "evidence_refs": ["current_message"],
            },
        },
        shared_context=_shared_context("多少钱"),
        sequences=[],
        checkpoint_taxonomy=[
            {
                "id": 10,
                "code": "price",
                "name": "价格/费用",
                "action_counts": {"campaign": 3},
                "tags": [
                    {
                        "id": 21,
                        "name": "活动内容介绍+抢购方式说明",
                        "action_counts": {"campaign": 3},
                    }
                ],
            }
        ],
    )
    result = _expand_sequence_action_queries(result, sequences=[])

    assert result["knowledge_focus"]["source"] == "none"
    assert result["script_queries"] == []


def test_sequence_candidates_order_exact_metadata_first_without_hiding_other_real_sequences() -> None:
    sequences = [
        _sequence(),
        {"id": "19", "checkpoint_code": "all", "steps": []},
        {"id": "20", "checkpoint_code": "price", "steps": []},
    ]

    exact = _sequences_for_checkpoint(
        sequences,
        {"checkpoint": {"primary_code": "distance"}},
    )
    fallback = _sequences_for_checkpoint(
        sequences,
        {"checkpoint": {"primary_code": "inquiry"}},
    )

    assert [item["id"] for item in exact] == ["18", "19", "20"]
    assert [item["id"] for item in fallback] == ["19", "18", "20"]


def test_sequence_candidates_keep_legacy_code_sequence_visible_to_model() -> None:
    sequences = [
        {"id": "13", "checkpoint_code": "time_conflict", "steps": []},
        {"id": "6", "checkpoint_code": "effect", "steps": []},
    ]

    result = _sequences_for_checkpoint(
        sequences,
        {"checkpoint": {"primary_type_id": 18, "primary_code": "cp2"}},
    )

    assert [item["id"] for item in result] == ["13", "6"]


def test_router_accepts_tenant_owned_checkpoint_type_and_tag() -> None:
    sequence = {
        "id": "91",
        "checkpoint_code": "skin_tone",
        "steps": [{"id": "911", "action_code": "case"}],
    }
    raw = {
        "classification_status": "clear",
        "checkpoint": {
            "primary_type_id": 99,
            "primary_code": "skin_tone",
            "primary_type_name": "肤色诉求",
            "primary_tag_id": 501,
            "primary_tag_name": "只想提亮",
            "evidence_refs": ["current_message"],
        },
        "sequence_match": {
            "sequence_ids": ["91"],
            "relevant_step_ids": ["911"],
        },
    }

    result = _normalize_semantic_route(
        raw,
        shared_context=_shared_context("我主要想提亮肤色"),
        sequences=[sequence],
        checkpoint_taxonomy=[
            {
                "id": 99,
                "code": "skin_tone",
                "name": "肤色诉求",
                "tags": [{"id": 501, "name": "只想提亮"}],
            }
        ],
    )
    result = _expand_sequence_action_queries(result, sequences=[sequence])

    assert result["checkpoint"]["primary_type_id"] == 99
    assert result["checkpoint"]["primary_tag_id"] == 501
    assert result["script_queries"][0]["checkpoint_type_id"] == 99
    assert result["script_queries"][0]["checkpoint_tag_id"] == 501


def test_script_content_candidates_preserve_each_paragraph_message_order() -> None:
    candidates = script_content_candidates(
        {
            "candidates": [
                {
                    "script_id": "172",
                    "source_id": "D27",
                    "source_ref": "follow_script:172",
                    "script_name": "距离价值",
                    "checkpoint_type": {"id": 9, "code": "distance", "name": "距离/便利"},
                    "checkpoint_tag": {"id": 1, "name": "太远不方便"},
                    "action_code": "case",
                    "action_name": "效果案例",
                    "paragraphs": [
                        {
                            "paragraph_no": 1,
                            "messages": [
                                {"type": "text", "content": "先看这个案例"},
                                {"type": "image", "url": "https://cdn.example.com/a.png", "file_id": 88},
                                {"type": "text", "content": "这组和您的顾虑接近"},
                            ],
                        },
                        {
                            "paragraph_no": 2,
                            "messages": [
                                {"type": "video", "url": "https://cdn.example.com/b.mp4", "file_id": 89}
                            ],
                        },
                    ],
                }
            ]
        }
    )

    assert [item["content_id"] for item in candidates] == ["follow_script:D27:p1", "follow_script:D27:p2"]
    assert [item["source_ref"] for item in candidates] == [
        "follow_script:172:p1",
        "follow_script:172:p2",
    ]
    assert candidates[0]["selection_constraints"]["authority_scope"] == "approved_sales_expression"
    assert candidates[0]["selection_constraints"]["hard_fact_authority"] is False
    assert [item["type"] for item in candidates[0]["reference_messages"]] == ["text", "image", "text"]
    assert candidates[1]["required_structured_media"] == [
        {"type": "video", "content": "https://cdn.example.com/b.mp4"}
    ]


def test_deepseek_failure_uses_fixed_openai_fallback(monkeypatch) -> None:
    class _Fallback:
        available = True
        last_usage = {"model": "gpt-5.4-mini"}

        async def chat_json(self, messages, **kwargs):
            assert kwargs["tier"] == "fast"
            assert kwargs["max_parallel_candidates"] == 1
            assert any("json" in item["content"].lower() for item in messages)
            return {"checkpoint": {"primary_code": "price"}}

    settings = SimpleNamespace(
        deepseek_api_key="configured",
        deepseek_api_base_url="https://api.deepseek.com",
        deepseek_semantic_model="deepseek-v4-flash",
        deepseek_semantic_timeout_seconds=10,
        deepseek_semantic_max_tokens=800,
    )
    client = DeepSeekSemanticClient(settings, _Fallback())

    async def failed_direct(messages):
        del messages
        raise TimeoutError("direct timeout")

    monkeypatch.setattr(client, "_direct_json", failed_direct)
    async def scenario():
        result = await client.chat_json([{"role": "system", "content": "Return json."}])
        return result, client.last_usage

    result, usage = asyncio.run(scenario())

    assert result["checkpoint"]["primary_code"] == "price"
    assert usage["fallback_used"] is True
    assert "direct timeout" in usage["direct_error"]


@pytest.mark.parametrize(
    "content, expected",
    [
        ("上海浦东有店吗", True),
        ("价格多少", False),
    ],
)
def test_store_tool_plan_remains_model_routed(content: str, expected: bool) -> None:
    class _StoreSemantic(_SemanticClient):
        async def chat_json(self, messages):
            del messages
            return {
                "checkpoint": {"primary_code": "inquiry", "evidence_refs": ["current_message"]},
                "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
                "store_query": {
                    "required": expected,
                    "purpose": "store_resolution" if expected else "none",
                    "location_evidence_refs": ["current_message"] if expected else [],
                    "destination_hint": "上海浦东" if expected else "",
                },
                "script_queries": [],
            }

    service = V3SemanticRouterService(
        semantic_client=_StoreSemantic(),
        knowledge_client=_KnowledgeClient(),
    )
    result = asyncio.run(service.route(shared_context=_shared_context(content)))
    assert bool(result["tool_plan"]["tool_calls"]) is expected
    if expected:
        assert result["tool_plan"]["tool_calls"][0]["name"] == "resolve_customer_store"
        arguments = result["tool_plan"]["tool_calls"][0]["arguments"]
        assert arguments["use_resolver_admin_fallback"] is True
        assert arguments["allow_broad_scope_delivery"] is True


def test_store_tool_plan_drops_destination_hint_not_backed_by_cited_message() -> None:
    class _UnsupportedHintSemantic(_SemanticClient):
        async def chat_json(self, messages):
            del messages
            return {
                "checkpoint": {"primary_code": "distance", "evidence_refs": ["current_message"]},
                "sequence_match": {"sequence_ids": [], "relevant_step_ids": []},
                "store_query": {
                    "required": True,
                    "purpose": "nearest_store",
                    "location_evidence_refs": ["current_message"],
                    "destination_hint": "客户所在城市",
                },
                "script_queries": [],
            }

    service = V3SemanticRouterService(
        semantic_client=_UnsupportedHintSemantic(),
        knowledge_client=_KnowledgeClient(),
    )
    result = asyncio.run(service.route(shared_context=_shared_context("附近真的没有了吗？")))

    tool_call = result["tool_plan"]["tool_calls"][0]
    assert "destination_hint" not in tool_call["arguments"]


def test_sent_effect_images_are_filtered_but_activity_asset_remains_visible() -> None:
    assets = [
        {
            "content_id": "effect_pack",
            "asset_role": "effect_evidence",
            "messages": [
                {"type": "image", "content": {"url": "https://assets.example/effect-1.jpg"}},
                {"type": "image", "content": {"url": "https://assets.example/effect-2.jpg"}},
            ],
            "media": [
                {"type": "image", "content": {"url": "https://assets.example/effect-1.jpg"}},
                {"type": "image", "content": {"url": "https://assets.example/effect-2.jpg"}},
            ],
        },
        {
            "content_id": "activity_pack",
            "asset_role": "activity_offer",
            "messages": [{"type": "image", "content": {"url": "https://assets.example/activity.jpg"}}],
            "media": [{"type": "image", "content": {"url": "https://assets.example/activity.jpg"}}],
        },
    ]
    result = _v3_available_assets_for_turn(
        {"business_rules": {"offer": {"case_image_fallback_urls": []}}},
        assets,
        sent_summary={
            "activity_intro_image_sent": True,
            "case_image_delivery": {
                "total_events": 1,
                "last_sent_at": "2026-08-20T10:00:00+08:00",
                "sent_image_urls": ["https://assets.example/effect-1.jpg"],
            },
        },
    )

    by_id = {item["content_id"]: item for item in result}
    assert [message["content"]["url"] for message in by_id["effect_pack"]["media"]] == [
        "https://assets.example/effect-2.jpg"
    ]
    assert by_id["activity_pack"]["delivery_observation"]["sent_count"] == 1


def test_text_only_sop_assets_do_not_reenter_ordinary_v3_reply() -> None:
    assets = [
        {
            "content_id": "s10_deposit_close",
            "asset_role": "deposit_close",
            "messages": [
                {"type": "text", "content": {"text": "预约金说明"}},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
        }
    ]
    state = {
        "business_rules": {"offer": {"case_image_fallback_urls": []}},
        "customer_context": {"orders": []},
        "payment_state": "required_unpaid",
    }

    before_activity = _v3_available_assets_for_turn(
        state,
        assets,
        sent_summary={},
        sop_progress={"completed_pack_ids": []},
    )
    after_activity = _v3_available_assets_for_turn(
        state,
        assets,
        sent_summary={},
        sop_progress={"completed_pack_ids": ["s10_activity_intro"]},
    )

    assert before_activity == []
    assert after_activity == []


def test_location_capture_text_pack_stays_out_of_v3_media_assets() -> None:
    result = _v3_available_assets_for_turn(
        {"business_rules": {"offer": {"case_image_fallback_urls": []}}},
        [
            {
                "content_id": "s10_store_prompt",
                "asset_role": "location_capture",
                "messages": [{"type": "text", "content": {"text": "您在哪个城市"}}],
            },
            {
                "content_id": "s10_activity_intro",
                "asset_role": "activity_offer",
                "messages": [
                    {"type": "text", "content": {"text": "活动介绍"}},
                    {"type": "image", "content": {"url": "https://assets.example/activity.jpg"}},
                ],
                "media": [
                    {"type": "image", "content": {"url": "https://assets.example/activity.jpg"}}
                ],
            },
        ],
        sent_summary={},
        sop_progress={},
    )

    assert [item["content_id"] for item in result] == ["s10_activity_intro"]


def test_knowledge_focus_brings_configured_authoritative_fact_topic() -> None:
    result = _normalize_semantic_route(
        {
            "classification_status": "none",
            "current_intent": {
                "summary": "客户询问需要做几次",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "knowledge_focus": {
                "checkpoint_type_id": 11,
                "checkpoint_code": "effect",
                "checkpoint_tag_id": 5,
                "action_code": "resolve",
                "source": "current_intent",
                "evidence_refs": ["current_message"],
            },
            "relevant_fact_topic_ids": [],
        },
        shared_context=_shared_context("需要做几次"),
        sequences=[],
        checkpoint_taxonomy=[
            {
                "id": 11,
                "code": "effect",
                "name": "效果疑虑",
                "action_counts": {"resolve": 1},
                "tags": [
                    {
                        "id": 5,
                        "name": "一次能干净吗",
                        "action_counts": {"resolve": 1},
                    }
                ],
            }
        ],
        fact_topic_catalog=[
            {
                "id": "effect_evidence",
                "knowledge_checkpoint_codes": ["effect"],
            },
            {"id": "activity_offer"},
        ],
    )

    assert result["knowledge_focus"]["checkpoint_code"] == "effect"
    assert result["relevant_fact_topic_ids"] == ["effect_evidence"]


def test_router_prompt_separates_recovery_wait_and_operation_scope() -> None:
    assert "刚做过其他护理或项目" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "不归入普通“没时间、拖延到店”的 time_conflict 话术" in (
        V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    )
    assert "operation_feeling 只用于客户正在询问操作过程" in (
        V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    )
    assert "即使句子出现“操作”二字，也只选择 activity_offer" in (
        V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    )
    assert "按“客户需要什么类型的答案”选择" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "body_area 只用于客户明确询问脸、手、身体等实际操作部位" in (
        V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    )
