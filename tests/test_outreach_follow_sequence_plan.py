from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.services.follow_knowledge_client import (  # noqa: E402
    FollowKnowledgeClient,
    _normalize_sequence,
    is_supported_action_code,
)
from app.services.outreach.follow_sequence import (  # noqa: E402
    compact_follow_sequence_catalog,
    normalize_follow_sequence_decision,
    normalize_follow_sequence_schedule,
    rank_follow_scripts_for_node,
    select_uncompleted_follow_sequence_nodes,
    sequence_checksum,
)
from app.services.outreach.planning import (  # noqa: E402
    PlanGenerator,
    _personalized_sequence_plan_error,
)
from app.services.outreach.message import MessageGenerator  # noqa: E402
from app.services.outreach.first_day import (  # noqa: E402
    OutreachMessagePolicyError,
    _first_day_message_policy_error,
)


def _raw_sequence(step_count: int, *, action: str = "act022") -> dict[str, object]:
    return {
        "id": "sequence-1",
        "sequenceName": "效果信任跟进",
        "checkpointCode": "effect_trust",
        "checkpointName": "效果信任",
        "stepCount": step_count,
        "steps": [
            {
                "id": f"node-{index}",
                "sortOrder": index,
                "actionCode": action if index == step_count else "act003",
                "actionName": "案例证明" if index == step_count else "低压承接",
                "triggerBase": "last_reply",
                "relativeValue": (index - 1) * 5,
                "relativeUnit": "minute",
                "fixedTime": "",
                "remark": f"节点{index}",
            }
            for index in range(1, step_count + 1)
        ],
    }


def _plan_step(index: int, total: int) -> dict[str, object]:
    return {
        "step": index,
        "plan_mode": "follow_sequence",
        "source_id": f"follow-sequence-node:node-{index}",
        "delay_minutes": (index - 1) * 5,
        "schedule_source": {
            "trigger_base": "last_reply",
            "relative_minutes": (index - 1) * 5,
        },
        "message_goal": f"完成节点{index}",
        "no_reply_action": "end_plan" if index == total else "advance_to_next_step",
        "reply_messages": [{"type": "text", "order": 1, "content": {"text": f"节点{index}草稿"}}],
        "should_send_payment_collection": False,
        "follow_sequence": {
            "id": "sequence-1",
            "checksum": "checksum-1",
        },
        "follow_sequence_node": {
            "id": f"node-{index}",
            "action_code": "act022",
        },
        "content_sources": [f"follow-sequence-node:node-{index}"],
    }


def test_published_action_codes_are_forward_compatible_and_keep_every_node() -> None:
    assert is_supported_action_code("act019") is True
    assert is_supported_action_code("act038") is True
    assert is_supported_action_code("act039") is True
    assert is_supported_action_code("action-39") is False

    normalized = _normalize_sequence(_raw_sequence(7, action="act038"))
    assert normalized is not None
    assert normalized["step_count"] == 7
    assert len(normalized["steps"]) == 7
    assert normalized["steps"][-1]["action_code"] == "act038"


def test_selector_catalog_keeps_outline_without_expanding_all_nodes() -> None:
    sequence = _normalize_sequence(_raw_sequence(11, action="act038"))
    assert sequence is not None
    compact = compact_follow_sequence_catalog({"status": "ok", "total": 1, "items": [sequence]})
    assert compact["usable_total"] == 1
    assert compact["items"][0]["step_count"] == 11
    assert "nodes" not in compact["items"][0]
    assert len(compact["items"][0]["action_outline"]) <= 5


def test_selector_normalizes_common_stop_contact_alias_without_a_model_retry() -> None:
    decision = normalize_follow_sequence_decision(
        {
            "eligible": False,
            "hard_boundary": {
                "active": True,
                "type": "explicit_exit",
                "message_indexes": [0],
                "fact": "客户明确要求停止联系",
            },
            "decision_mode": "mainline",
            "checkpoint": {"code": "none", "name": "无卡点"},
            "selected_sequence_id": "",
            "mainline_tasks": [],
        }
    )
    assert decision["hard_boundary"]["type"] == "stop_contact"
    assert decision["sequence_match_scope"] == "none"


def test_invalid_sequence_node_rejects_the_whole_sequence_instead_of_dropping_it() -> None:
    raw = _raw_sequence(4)
    raw["steps"][2]["id"] = ""  # type: ignore[index]
    assert _normalize_sequence(raw) is None


def test_catalog_paging_uses_raw_count_instead_of_stopping_on_invalid_items() -> None:
    async def fetch_page(page: int) -> dict[str, object]:
        if page == 1:
            return {
                "status": "ok",
                "total": 110,
                "page_size": 100,
                "raw_item_count": 100,
                "invalid_item_count": 10,
                "items": [{"id": f"first-{index}"} for index in range(90)],
            }
        return {
            "status": "ok",
            "total": 110,
            "page_size": 100,
            "raw_item_count": 10,
            "invalid_item_count": 0,
            "items": [{"id": f"second-{index}"} for index in range(10)],
        }

    client = object.__new__(FollowKnowledgeClient)
    result = asyncio.run(client._query_all_pages(fetch_page, schema_version="follow_sequence_index_v2"))
    assert result["status"] == "ok"
    assert result["raw_item_count"] == 110
    assert result["invalid_item_count"] == 10
    assert len(result["items"]) == 100
    assert len(result["pages"]) == 2


def test_catalog_paging_turns_null_page_into_explicit_error() -> None:
    async def fetch_page(_: int) -> None:
        return None

    client = object.__new__(FollowKnowledgeClient)
    result = asyncio.run(
        client._query_all_pages(fetch_page, schema_version="follow_script_index_v2")
    )

    assert result == {
        "schema_version": "follow_script_index_v2",
        "status": "error",
        "reason": "page_query_invalid_response",
        "total": 0,
        "items": [],
        "pages": [],
        "duration_ms": result["duration_ms"],
    }


def test_follow_sequence_plan_contract_requires_one_task_per_source_node() -> None:
    snapshot = {
        "follow_sequence_selection": {
            "sequence_id": "sequence-1",
            "checksum": "checksum-1",
            "source_node_count": 5,
        },
        "first_day_sop_sequence": [],
    }
    response = {
        "should_create_plan": True,
        "plan_mode": "follow_sequence",
        "steps": [_plan_step(index, 5) for index in range(1, 6)],
    }
    assert _personalized_sequence_plan_error(response, source_snapshot=snapshot) == ""

    response["steps"] = response["steps"][:4]
    assert (
        _personalized_sequence_plan_error(response, source_snapshot=snapshot)
        == "follow sequence node count must equal materialized task count"
    )


def test_same_checkpoint_scripts_relax_action_without_returning_too_many() -> None:
    scripts = [
        {"id": "exact", "action_code": "act022", "action_name": "案例证明", "weight": 1},
        {"id": "other-high", "action_code": "act003", "action_name": "低压承接", "weight": 99},
        *[{"id": f"other-{index}", "action_code": "act004", "weight": index} for index in range(10)],
    ]
    selected = rank_follow_scripts_for_node(
        scripts,
        node={"action_code": "act022", "action_name": "案例证明"},
        limit=6,
    )
    assert len(selected) == 6
    assert selected[0]["id"] == "exact"
    assert "other-high" in {item["id"] for item in selected}


def test_script_ranking_excludes_used_ids_and_prioritizes_current_node_action() -> None:
    scripts = [
        {
            "id": "used",
            "action_code": "act005",
            "script_name": "重复解决疑虑",
            "body_text": "距离太远需要考虑",
            "weight": 100,
        },
        {
            "id": "same-query-wrong-action",
            "action_code": "act013",
            "script_name": "距离顾虑",
            "body_text": "距离太远需要考虑",
            "weight": 99,
        },
        {
            "id": "current-action",
            "action_code": "act002",
            "script_name": "活动邀约",
            "body_text": "活动名额与到店安排",
            "weight": 1,
        },
    ]

    selected = rank_follow_scripts_for_node(
        scripts,
        node={"action_code": "act002", "action_name": "活动邀约"},
        query_text="客户觉得距离太远",
        exclude_script_ids={"used"},
        limit=3,
    )

    assert [item["id"] for item in selected][:1] == ["current-action"]
    assert "used" not in {item["id"] for item in selected}


def test_sequence_progress_collapses_duplicate_nodes_resumes_and_keeps_conversion_exit() -> None:
    sequence = {
        "id": "sequence-1",
        "steps": [
            {"id": "n1", "action_code": "a1", "action_name": "共情", "remark": "理解顾虑"},
            {"id": "n2", "action_code": "a2", "action_name": "解释", "remark": "解释距离"},
            {"id": "n3", "action_code": "a2", "action_name": "解释", "remark": "解释距离"},
            {"id": "n4", "action_code": "a3", "action_name": "案例", "remark": "给案例"},
            {"id": "n5", "action_code": "a4", "action_name": "证明", "remark": "社会证明"},
            {"id": "n6", "action_code": "a5", "action_name": "邀约", "remark": "推进到店"},
        ],
    }
    checksum = sequence_checksum(sequence)

    result = select_uncompleted_follow_sequence_nodes(
        sequence["steps"],
        sequence_id="sequence-1",
        checksum=checksum,
        recent_outreach_delivery=[
            {
                "follow_sequence_id": "sequence-1",
                "follow_sequence_checksum": checksum,
                "follow_sequence_node_id": "n1",
            }
        ],
    )

    assert [item["id"] for item in result["nodes"]] == ["n2", "n4", "n5", "n6"]
    assert result["duplicate_node_ids"] == ["n3"]
    assert result["completed_node_ids"] == ["n1"]


def test_follow_plan_materializes_three_distinct_checkpoint_steps_and_one_conversion_exit() -> None:
    class Catalog:
        available = True

        @staticmethod
        async def query_all_scripts() -> dict[str, object]:
            return {
                "status": "ok",
                "items": [
                    {
                        "id": f"script-{index}",
                        "checkpoint_code": "distance",
                        "action_code": f"a{index}",
                        "action_name": name,
                        "body_text": f"{name}的新话术",
                        "weight": 10,
                    }
                    for index, name in enumerate(("共情", "解释", "案例", "证明", "邀约"), start=1)
                ],
            }

    sequence = {
        "id": "sequence-1",
        "sequence_name": "距离跟进",
        "checkpoint_code": "distance",
        "checkpoint_name": "距离",
        "steps": [
            {"id": "n1", "action_code": "a1", "action_name": "共情", "remark": "理解顾虑"},
            {"id": "n2", "action_code": "a2", "action_name": "解释", "remark": "解释距离"},
            {"id": "n3", "action_code": "a2", "action_name": "解释", "remark": "解释距离"},
            {"id": "n4", "action_code": "a3", "action_name": "案例", "remark": "给案例"},
            {"id": "n5", "action_code": "a4", "action_name": "证明", "remark": "社会证明"},
            {"id": "n6", "action_code": "a5", "action_name": "邀约", "remark": "推进到店"},
        ],
    }
    checksum = sequence_checksum(sequence)
    planner = PlanGenerator(
        repository=object(),
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
        follow_knowledge_client=Catalog(),
    )
    source_snapshot = {
        "follow_sequence_catalog": {"status": "ok"},
        "recent_outreach_delivery": [
            {
                "follow_sequence_id": "sequence-1",
                "follow_sequence_checksum": checksum,
                "follow_sequence_node_id": "n1",
                "selected_script_id": "script-1",
                "reply_messages": [
                    {"type": "text", "content": {"text": "之前已经共情过距离问题。"}}
                ],
            }
        ],
    }

    result = asyncio.run(
        planner._build_follow_sequence_plan(
            source_snapshot=source_snapshot,
            decision={
                "sequence_match_scope": "exact_checkpoint",
                "script_search_query": "距离远",
                "checkpoint": {"name": "距离", "evidence": "太远了"},
                "customer_mainline": {"next_business_action": "确认方便到店的时间"},
            },
            sequence=sequence,
        )
    )

    assert [step["follow_sequence_node"]["id"] for step in result["steps"]] == [
        "n2",
        "n4",
        "n5",
        "n6",
    ]
    assert [step["conversion_step"] for step in result["steps"]] == [False, False, False, True]
    assert result["steps"][-1]["conversion_goal"] == "确认方便到店的时间"
    assert source_snapshot["follow_sequence_selection"]["catalog_node_count"] == 6
    assert source_snapshot["follow_sequence_selection"]["duplicate_node_ids"] == ["n3"]


def test_repeat_policy_checks_later_steps_against_local_outreach_history() -> None:
    error, evidence = _first_day_message_policy_error(
        ["很多顾客也是专程过来，实际体验后都觉得值得。"],
        step_index=3,
        plan={"source_snapshot": {}},
        context={
            "recent_outreach_delivery": [
                {
                    "reply_messages": [
                        {
                            "type": "text",
                            "content": {"text": "很多顾客也是专程过来，实际体验后都觉得值得。"},
                        }
                    ]
                }
            ]
        },
    )

    assert error == "first_day_message_too_similar_to_history"
    assert "专程过来" in evidence


def test_progressive_script_retrieval_reserves_room_for_global_semantic_candidates() -> None:
    scripts = [
        *[
            {
                "id": f"price-{index}",
                "checkpoint_code": "cp10",
                "checkpoint_name": "价格卡点",
                "action_code": "act013",
                "script_name": "低价真实性解释",
                "body_text": "解释为什么活动价格低",
                "weight": 100 - index,
            }
            for index in range(8)
        ],
        {
            "id": "budget-fit",
            "checkpoint_code": "cp6",
            "checkpoint_name": "需求不匹配",
            "checkpoint_tag": {"name": "客户预算较低"},
            "action_code": "act003",
            "script_name": "低预算也能改善",
            "body_text": "结合预算说明性价比和可获得的改善",
            "weight": 1,
        },
    ]
    selected = rank_follow_scripts_for_node(
        scripts,
        node={"action_code": "act013", "action_name": "共情引导"},
        checkpoint_code="cp10",
        query_text="客户觉得价格太贵，预算不足，希望了解性价比",
        limit=6,
    )
    assert len(selected) <= 6
    budget = next(item for item in selected if item["id"] == "budget-fit")
    assert budget["outreach_match_scope"] == "semantic_global"


def test_progressive_retrieval_weights_business_labels_above_body_noise() -> None:
    scripts = [
        {
            "id": "local",
            "checkpoint_code": "price",
            "action_code": "act001",
            "script_name": "价格说明",
            "body_text": "说明活动价格",
        },
        {
            "id": "body-noise-1",
            "checkpoint_code": "other-1",
            "action_code": "act002",
            "script_name": "其他问题",
            "body_text": "价格贵预算有限预算有限预算有限",
        },
        {
            "id": "body-noise-2",
            "checkpoint_code": "other-2",
            "action_code": "act003",
            "script_name": "其他问题",
            "body_text": "价格贵预算有限预算有限",
        },
        {
            "id": "metadata-fit",
            "checkpoint_code": "other-3",
            "checkpoint_tag": {"name": "预算有限"},
            "action_code": "act004",
            "script_name": "预算顾虑承接",
            "body_text": "先理解客户再解释价值",
        },
    ]

    selected = rank_follow_scripts_for_node(
        scripts,
        node={"action_code": "act001", "action_name": "价格说明"},
        checkpoint_code="price",
        query_text="客户觉得贵，预算有限",
        limit=3,
    )

    assert "metadata-fit" in {item["id"] for item in selected}


def test_follow_sequence_plan_tolerates_invalid_script_catalog_response() -> None:
    class NullScriptCatalog:
        available = True

        async def query_all_scripts(self) -> None:
            return None

    planner = PlanGenerator(
        repository=object(),
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
        follow_knowledge_client=NullScriptCatalog(),
    )
    source_snapshot = {"follow_sequence_catalog": {"status": "ok"}}
    result = asyncio.run(
        planner._build_follow_sequence_plan(
            source_snapshot=source_snapshot,
            decision={
                "sequence_match_scope": "checkpoint_type",
                "checkpoint": {"name": "距离卡点", "evidence": "客户觉得远"},
            },
            sequence={
                "id": "45",
                "sequence_name": "距离卡点跟进",
                "checkpoint_code": "cp9",
                "checkpoint_name": "距离卡点",
                "steps": [
                    {
                        "id": "node-1",
                        "action_code": "act003",
                        "action_name": "价值承接",
                        "remark": "说明专程到店的价值",
                    }
                ],
            },
        )
    )

    assert result["should_create_plan"] is True
    assert len(result["steps"]) == 1
    assert source_snapshot["follow_sequence_selection"]["script_catalog_status"] == "error"
    assert (
        source_snapshot["follow_sequence_selection"]["script_catalog_reason"]
        == "follow_script_catalog_invalid_response"
    )


def test_follow_sequence_tasks_do_not_require_legacy_scene_analysis() -> None:
    planner = PlanGenerator(
        repository=object(),
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
    )
    source_snapshot = {
        "first_day_workflow": {"strategy_decision": {"decision_mode": "follow_sequence"}},
        "first_day_sop_sequence": [],
        "conversation_activity": {
            "latest_customer_message_at": "2026-09-07T15:00:00+00:00",
            "latest_staff_message_at": "2026-09-07T15:01:00+00:00",
        },
    }
    response = {
        "should_create_plan": True,
        "plan_mode": "follow_sequence",
        "plan_arc": "距离卡点",
        "steps": [
            {
                "step": 1,
                "scene": "objection_resolution",
                "source_id": "follow-sequence-node:251",
                "delay_minutes": 10,
                "schedule_source": {"relative_minutes": 10},
                "intent": "解决疑虑",
                "message_goal": "提供距离顾虑的新价值",
                "reply_messages": [
                    {"type": "text", "content": {"text": "很多客户会专程过来，主要看重技术和效果。"}}
                ],
                "follow_sequence": {"id": "45"},
                "follow_sequence_node": {"id": "251"},
                "follow_script_candidates": [{"id": "72"}],
            }
        ],
    }

    raw_steps, tasks = asyncio.run(
        planner._materialize_tasks(
            {
                "first_day_trigger": True,
                "asset_catalog": [],
                "recent_media": {"urls": []},
                "activity_quote_fact": {},
                "payment_collection_gate": {},
                "source_snapshot": source_snapshot,
            },
            response,
        )
    )

    assert len(raw_steps) == 1
    assert len(tasks) == 1
    assert tasks[0]["content_sources"][-3]["outreach_task_metadata"]["follow_sequence"]["id"] == "45"


def test_night_active_plan_keeps_all_nodes_inside_customer_40_minute_window() -> None:
    latest_customer = datetime(2026, 9, 7, 14, 10, tzinfo=timezone.utc)  # 22:10 Beijing
    now = latest_customer + timedelta(minutes=1)
    steps = [
        {
            "delay_minutes": index * 30,
            "schedule_source": {
                "trigger_base": "last_reply",
                "relative_minutes": index * 30,
            },
        }
        for index in range(11)
    ]
    schedule = normalize_follow_sequence_schedule(
        now.isoformat(),
        steps,
        source_snapshot={
            "conversation_activity": {
                "latest_customer_message_at": latest_customer.isoformat(),
                "latest_staff_message_at": now.isoformat(),
            }
        },
    )
    deadline = latest_customer + timedelta(minutes=40)
    assert len(schedule) == 11
    assert {item["schedule_mode"] for item in schedule} == {"night_active_compressed"}
    assert all(datetime.fromisoformat(item["scheduled_at"]) <= deadline for item in schedule)


def test_inactive_night_plan_moves_the_first_node_to_morning_and_preserves_gap() -> None:
    now = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)  # 22:30 Beijing
    schedule = normalize_follow_sequence_schedule(
        now.isoformat(),
        [
            {"delay_minutes": 0, "schedule_source": {"relative_minutes": 0}},
            {"delay_minutes": 10, "schedule_source": {"relative_minutes": 10}},
        ],
        source_snapshot={
            "conversation_activity": {
                "latest_customer_message_at": (now - timedelta(hours=2)).isoformat(),
                "latest_staff_message_at": now.isoformat(),
            }
        },
    )
    first = datetime.fromisoformat(schedule[0]["scheduled_at"])
    second = datetime.fromisoformat(schedule[1]["scheduled_at"])
    assert first.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M") == "08:30"
    assert second - first == timedelta(minutes=10)
    assert {item["schedule_mode"] for item in schedule} == {"quiet_hours_deferred"}


def test_quiet_hours_overdue_follow_sequence_preserves_node_offsets() -> None:
    now = datetime(2026, 9, 7, 16, 30, tzinfo=timezone.utc)  # 00:30 Beijing
    latest_staff = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    schedule = normalize_follow_sequence_schedule(
        now.isoformat(),
        [
            {"delay_minutes": 0, "schedule_source": {"trigger_base": "last_reply", "relative_minutes": 0}},
            {"delay_minutes": 30, "schedule_source": {"trigger_base": "last_reply", "relative_minutes": 30}},
            {"delay_minutes": 180, "schedule_source": {"trigger_base": "last_reply", "relative_minutes": 180}},
        ],
        source_snapshot={
            "conversation_activity": {
                "latest_customer_message_at": (latest_staff - timedelta(minutes=10)).isoformat(),
                "latest_staff_message_at": latest_staff.isoformat(),
            }
        },
    )
    first = datetime.fromisoformat(schedule[0]["scheduled_at"])
    second = datetime.fromisoformat(schedule[1]["scheduled_at"])
    third = datetime.fromisoformat(schedule[2]["scheduled_at"])
    assert first.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M") == "08:30"
    assert second - first == timedelta(minutes=30)
    assert third - first == timedelta(minutes=180)
    assert {item["schedule_mode"] for item in schedule} == {"quiet_hours_deferred"}


def test_generate_plan_recovers_after_committed_plan_post_commit_error() -> None:
    class Repository:
        def __init__(self) -> None:
            self.updates: list[dict[str, object]] = []

        @staticmethod
        def get_first_day_outreach_run(
            workflow_run_id: str,
            *,
            include_related: bool = False,
        ) -> dict[str, object]:
            return {
                "workflow_run_id": workflow_run_id,
                "status": "running",
                "retry_count": 0,
                "plan_id": "plan-committed",
            }

        def update_first_day_outreach_run(self, _workflow_run_id: str, **changes: object) -> None:
            self.updates.append(changes)

    repository = Repository()
    planner = PlanGenerator(
        repository=repository,
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
    )

    async def fail_after_commit(**_: object) -> dict[str, object]:
        raise RuntimeError("post commit readback failed")

    planner._build_plan = fail_after_commit  # type: ignore[method-assign]
    result = asyncio.run(
        planner.generate_plan(
            customer_id="customer-1",
            corp_id="corp-1",
            wechat="SL8003",
            external_userid="external-1",
            workflow_run_id="run-1",
        )
    )

    assert result["created"] is True
    assert result["plan"]["id"] == "plan-committed"
    assert any(update.get("reason_code") == "plan_created_with_post_commit_warning" for update in repository.updates)


class _CaptureRepository:
    def __init__(self) -> None:
        self.tasks: list[dict[str, object]] = []

    def create_outreach_plan(self, **values: object) -> dict[str, object]:
        self.tasks = list(values["tasks"])  # type: ignore[arg-type]
        return {"plan": {"id": "plan-1"}, "tasks": self.tasks}

    def add_outreach_event(self, **_: object) -> None:
        return None


def test_materialization_does_not_truncate_a_five_node_sequence() -> None:
    repository = _CaptureRepository()
    planner = PlanGenerator(
        repository=repository,
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
    )
    response = {
        "should_create_plan": True,
        "plan_mode": "follow_sequence",
        "conversion_stage": "opened_silence",
        "stall_reason": "效果顾虑",
        "customer_psychology": "效果信任",
        "plan_goal": "处理卡点",
        "plan_arc": "效果信任跟进",
        "steps": [_plan_step(index, 5) for index in range(1, 6)],
    }
    now = datetime.now(timezone.utc)
    source_snapshot = {
        "conversation_activity": {
            "latest_customer_message_at": (now - timedelta(hours=2)).isoformat(),
            "latest_staff_message_at": now.isoformat(),
        },
        "follow_sequence_selection": {
            "sequence_id": "sequence-1",
            "checksum": "checksum-1",
            "source_node_count": 5,
        },
        "first_day_sop_sequence": [],
        "trigger_context": {"trigger_type": "first_day_opened_silence"},
    }
    result = asyncio.run(
        planner._materialize_plan(
            {
                "customer_id": "customer-1",
                "corp_id": "corp-1",
                "user_id": "user-1",
                "wechat": "SL8003",
                "external_userid": "external-1",
                "sop_plan_id": "first_day_opened_silence",
                "trigger_context": source_snapshot["trigger_context"],
                "workflow_run_id": "",
                "first_day_trigger": True,
                "reply_wait_minutes": 1,
                "customer_silence_minutes": 2,
                "activity_quote_fact": {},
                "asset_catalog": [],
                "recent_media": {},
                "payment_collection_gate": {},
                "source_snapshot": source_snapshot,
            },
            response,
        )
    )
    assert result["created"] is True
    assert len(repository.tasks) == 5
    assert source_snapshot["follow_sequence_selection"]["materialized_task_count"] == 5


class _MessageRepository:
    def recent_customer_context(self, *_: object, **__: object) -> dict[str, object]:
        return {"recent_messages": []}


class _MessageModel:
    async def chat_json(self, *_: object, **__: object) -> dict[str, object]:
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "content": {"text": "这个位置确实需要多花一点路程，我们不少客户看中的还是技术和效果。"},
                }
            ],
            "selected_script_id": "script-1",
            "script_rejection_reason": "",
            "value_dimension": "case_proof",
            "new_information": "提供一条新的实际效果证据",
            "conversion_action": "none",
        }


def test_follow_node_requires_real_script_selection_and_attaches_its_media() -> None:
    script = {
        "id": "script-1",
        "script_code": "distance-value",
        "script_name": "距离价值承接",
        "checkpoint_code": "distance",
        "action_code": "act022",
        "paragraphs": [
            {
                "paragraph_no": 1,
                "messages": [
                    {"type": "text", "content": "不少客户看中的是技术和效果。"},
                    {
                        "type": "image",
                        "url": "https://cdn.example.com/case.jpg",
                        "title": "效果参考",
                    },
                ],
            }
        ],
    }
    task = {
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "step_index": 1,
        "intent": "处理距离卡点",
        "message_goal": "用价值承接距离顾虑",
        "reply_messages": [{"type": "text", "content": {"text": "处理距离顾虑"}}],
        "content_source_metadata": [
            {
                "outreach_task_metadata": {
                    "plan_mode": "follow_sequence",
                    "conversion_step": False,
                    "follow_sequence_node": {
                        "id": "node-1",
                        "action_code": "act022",
                    },
                    "follow_script_candidates": [script],
                    "follow_script_model_candidates": [script],
                }
            }
        ],
    }
    result = asyncio.run(
        MessageGenerator(
            repository=_MessageRepository(),
            model_client=_MessageModel(),
        )._generate_task_messages(
            task=task,
            plan={"source_snapshot": {"trigger_context": {"trigger_type": "first_day_opened_silence"}}},
        )
    )
    assert isinstance(result, dict)
    assert result["selected_script_id"] == "script-1"
    assert [item["type"] for item in result["reply_messages"]] == ["text", "image"]


def test_message_generation_excludes_previously_sent_script_and_dimension() -> None:
    class Repository(_MessageRepository):
        @staticmethod
        def recent_outreach_delivery(**_: object) -> list[dict[str, object]]:
            return [
                {
                    "selected_script_id": "script-used",
                    "value_dimension": "empathy",
                    "reply_messages": [
                        {"type": "text", "content": {"text": "之前已经理解过客户的距离顾虑。"}}
                    ],
                }
            ]

    class Model:
        payload: dict[str, object] = {}

        async def chat_json(self, messages: list[dict[str, str]], **_: object) -> dict[str, object]:
            self.payload = json.loads(messages[1]["content"])
            return {
                "reply_messages": [
                    {"type": "text", "content": {"text": "导航显示实际通行时间并不长，您可以先看看路线。"}}
                ],
                "selected_script_id": "script-new",
                "script_rejection_reason": "",
                "value_dimension": "fact_explanation",
                "new_information": "提供实际通行时间这一决策依据",
                "conversion_action": "none",
            }

    candidates = [
        {"id": "script-used", "script_name": "旧共情"},
        {"id": "script-new", "script_name": "通行时间"},
    ]
    task = {
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "step_index": 2,
        "message_goal": "提供不同价值",
        "reply_messages": [{"type": "text", "content": {"text": "提供新事实"}}],
        "content_source_metadata": [
            {
                "outreach_task_metadata": {
                    "plan_mode": "follow_sequence",
                    "conversion_step": False,
                    "follow_script_candidates": candidates,
                    "follow_script_model_candidates": candidates,
                }
            }
        ],
    }
    model = Model()

    result = asyncio.run(
        MessageGenerator(repository=Repository(), model_client=model)._generate_task_messages(
            task=task,
            plan={"source_snapshot": {"trigger_context": {"trigger_type": "first_day_opened_silence"}}},
        )
    )

    assert isinstance(result, dict)
    assert result["selected_script_id"] == "script-new"
    metadata = model.payload["task_metadata"]
    assert [item["id"] for item in metadata["follow_script_candidates"]] == ["script-new"]
    assert metadata["used_script_ids"] == ["script-used"]
    assert metadata["used_value_dimensions"] == ["empathy"]
    prompt_history = model.payload["customer_context"]["recent_outreach_delivery"]
    assert prompt_history[0]["texts"] == ["之前已经理解过客户的距离顾虑。"]
    assert "reply_messages" not in prompt_history[0]


def test_conversion_step_returns_to_one_real_mainline_source() -> None:
    class Model:
        async def chat_json(self, *_: object, **__: object) -> dict[str, object]:
            return {
                "reply_messages": [
                    {"type": "text", "content": {"text": "我把活动内容给您说清楚，您再看是否合适。"}}
                ],
                "selected_script_id": "",
                "script_rejection_reason": "卡点已充分承接，回到未完成主线",
                "selected_mainline_source_id": "sop-pack:activity",
                "value_dimension": "activity_value",
                "new_information": "补齐尚未交付的活动内容",
                "conversion_action": "return_mainline",
            }

    task = {
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "step_index": 4,
        "message_goal": "回到未完成主线",
        "reply_messages": [{"type": "text", "content": {"text": "回主线"}}],
        "content_source_metadata": [
            {
                "outreach_task_metadata": {
                    "plan_mode": "follow_sequence",
                    "conversion_step": True,
                    "follow_script_candidates": [{"id": "checkpoint-script"}],
                    "follow_script_model_candidates": [{"id": "checkpoint-script"}],
                    "conversion_mainline_sources": [
                        {
                            "source_id": "sop-pack:activity",
                            "mapped_scene": "activity_intro",
                            "texts": ["线上活动内容"],
                        }
                    ],
                }
            }
        ],
    }

    result = asyncio.run(
        MessageGenerator(repository=_MessageRepository(), model_client=Model())._generate_task_messages(
            task=task,
            plan={"source_snapshot": {"trigger_context": {"trigger_type": "first_day_opened_silence"}}},
        )
    )

    assert isinstance(result, dict)
    assert result["selected_mainline_source_id"] == "sop-pack:activity"
    assert result["conversion_action"] == "return_mainline"


def test_conversion_step_cannot_ask_for_deposit_before_activity_quote() -> None:
    class Model:
        async def chat_json(self, *_: object, **__: object) -> dict[str, object]:
            return {
                "reply_messages": [
                    {"type": "text", "content": {"text": "需要我现在帮您锁定名额吗？"}}
                ],
                "selected_script_id": "script-1",
                "script_rejection_reason": "",
                "selected_mainline_source_id": "",
                "value_dimension": "deposit_intent",
                "new_information": "询问锁定名额",
                "conversion_action": "ask_deposit_intent",
            }

    task = {
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "step_index": 4,
        "message_goal": "推进成交",
        "reply_messages": [{"type": "text", "content": {"text": "推进成交"}}],
        "content_source_metadata": [
            {
                "outreach_task_metadata": {
                    "plan_mode": "follow_sequence",
                    "conversion_step": True,
                    "follow_script_candidates": [{"id": "script-1"}],
                    "follow_script_model_candidates": [{"id": "script-1"}],
                    "conversion_mainline_sources": [],
                }
            }
        ],
    }

    with pytest.raises(OutreachMessagePolicyError, match="deposit_intent_requires_activity_quote"):
        asyncio.run(
            MessageGenerator(repository=_MessageRepository(), model_client=Model())._generate_task_messages(
                task=task,
                plan={
                    "source_snapshot": {
                        "trigger_context": {"trigger_type": "first_day_opened_silence"},
                        "activity_quote_fact": {"completed": False},
                    }
                },
            )
        )
