from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.prompts.v3_semantic_router import (
    V3_SCRIPT_SELECTOR_SYSTEM_PROMPT,
    V3_SEMANTIC_ROUTER_SYSTEM_PROMPT,
    build_v3_semantic_router_messages,
)
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.v3_semantic_router_service import (
    V3SemanticRouterService,
    _expand_sequence_action_queries,
    _normalize_semantic_route,
)
from app.graph.nodes.parallel_reply_chain import _v3_available_assets_for_turn


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


def test_selected_sequence_exposes_each_distinct_action_without_choosing_for_reply() -> None:
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
        ("case", "182"),
    ]


class _SemanticClient:
    available = True

    def __init__(self, *, selected_scripts: list[str] | None = None) -> None:
        self.calls = 0
        self.selected_scripts = selected_scripts or []
        self.last_usage = {"model": "deepseek-v4-flash"}

    async def chat_json(self, messages):
        self.calls += 1
        if "参考话术检索器" in messages[0]["content"]:
            return {"selected_script_ids": self.selected_scripts, "reason": "互补证据"}
        return {
            "checkpoint": {
                "primary_code": "distance",
                "secondary_code": "",
                "evidence_refs": ["conv_002"],
                "reason": "客户明确认为最近门店仍远",
            },
            "sequence_match": {
                "sequence_ids": ["18", "unknown"],
                "relevant_step_ids": ["181", "unknown-step"],
                "reason": "先承接距离，再换价值维度",
            },
            "store_query": {
                "required": False,
                "purpose": "none",
                "location_evidence_refs": [],
            },
            "script_queries": [
                {
                    "checkpoint_code": "distance",
                    "action_code": "empathy",
                    "sequence_id": "18",
                    "step_id": "181",
                },
                {
                    "checkpoint_code": "distance",
                    "action_code": "resolve",
                    "sequence_id": "18",
                    "step_id": "181",
                },
            ],
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

    async def query_all_scripts(self, *, checkpoint_code: str, action_code: str):
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
    semantic = _SemanticClient()
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
            "checkpoint_code": "distance",
            "action_code": "empathy",
            "sequence_id": "18",
            "step_id": "181",
        },
        {
            "checkpoint_code": "distance",
            "action_code": "case",
            "sequence_id": "18",
            "step_id": "182",
            "query_source": "selected_sequence_action_coverage",
        },
    ]
    assert knowledge.script_queries == [("distance", "empathy"), ("distance", "case")]
    assert result["knowledge_evidence"]["candidate_count"] == 2
    selected_sequence = result["knowledge_evidence"]["sequence_candidates"][0]
    assert selected_sequence["selection_reason"] == "先承接距离，再换价值维度"
    assert "description" not in selected_sequence
    assert "objective" not in selected_sequence["steps"][0]
    assert result["tool_plan"]["decision"] == "facts_sufficient"
    assert semantic.calls == 1


def test_router_uses_second_stage_only_above_threshold() -> None:
    semantic = _SemanticClient(selected_scripts=["D02", "D05", "unknown"])
    service = V3SemanticRouterService(
        semantic_client=semantic,
        knowledge_client=_KnowledgeClient(script_count=13),
        script_threshold=12,
        max_scripts=6,
    )

    result = asyncio.run(service.route(shared_context=_shared_context()))

    assert semantic.calls == 2
    assert result["knowledge_evidence"]["script_option_count"] == 13
    assert [item["source_id"] for item in result["knowledge_evidence"]["candidates"]] == ["D02", "D05"]


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


def test_semantic_prompts_do_not_delegate_customer_reply_or_close_decision() -> None:
    assert "不得生成客户话术" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不得判断是否成交、发预约金卡" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "单纯问某地有无门店" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "单纯说城市、找店、问地址不属于 distance" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不得写“客户所在城市、当前位置、附近”等占位词" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "destination_hint 必须逐字来自所引用的客户消息" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "动作完成只影响检索优先级" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不要只重复已经完成的 empathy/resolve" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"classification_status":"clear | ambiguous | none"' in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "明确终止联系。它不是 hesitation" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "确认或应答，本身没有提出新问题或顾虑" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不得用更早历史中的旧问题强行生成卡点" in V3_SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "不生成客户话术，不决定成交动作" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "通常只保留 2–3 条逻辑互补的候选" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    assert "同一结论只是换措辞不算互补" in V3_SCRIPT_SELECTOR_SYSTEM_PROMPT
    rendered = build_v3_semantic_router_messages(shared_context=_shared_context(), sequence_index=[_sequence()])
    assert "完整聊天" in rendered[1]["content"]
    assert "18｜distance" in rendered[1]["content"]
    assert "距离无法改善时换到效果和活动价值" in rendered[1]["content"]
    assert len(rendered[1]["content"]) < 6000


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


def test_deposit_asset_is_hidden_until_activity_delivery_is_structurally_proven() -> None:
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

    unavailable = _v3_available_assets_for_turn(
        state,
        assets,
        sent_summary={},
        sop_progress={"completed_pack_ids": []},
    )
    available = _v3_available_assets_for_turn(
        state,
        assets,
        sent_summary={},
        sop_progress={"completed_pack_ids": ["s10_activity_intro"]},
    )

    assert unavailable == []
    assert [item["content_id"] for item in available] == ["s10_deposit_close"]
