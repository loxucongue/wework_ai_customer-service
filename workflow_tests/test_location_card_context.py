from __future__ import annotations

from app.graph.nodes.location_card import append_location_card_to_content
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model


def _location_context() -> dict[str, str]:
    return {
        "msgtype": "location",
        "location": "24.451232,118.088724",
        "location_title": "厦门大学附属第一医院",
        "location_address": "福建省厦门市思明区镇海路55号",
        "location_zoom": "15",
    }


def test_location_card_appends_fact_block_to_current_message() -> None:
    normalized, card = append_location_card_to_content("门店位置：厦门大学附属第一医院", _location_context())

    assert normalized.startswith("定位卡片：厦门大学附属第一医院")
    assert not normalized.startswith("门店位置：")
    assert "【客户发送定位卡片】" in normalized
    assert "标题：厦门大学附属第一医院" in normalized
    assert "地址：福建省厦门市思明区镇海路55号" in normalized
    assert "坐标：24.451232,118.088724" in normalized
    assert card["title"] == "厦门大学附属第一医院"


def test_planner_and_reply_payload_expose_location_card() -> None:
    normalized, _ = append_location_card_to_content("门店位置：厦门大学附属第一医院", _location_context())
    state = {
        "content": "门店位置：厦门大学附属第一医院",
        "normalized_content": normalized,
        "request_context": _location_context(),
        "conversation_history": [],
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)

    assert planner_payload["current_message"] == normalized
    assert planner_payload["location_card"]["address"] == "福建省厦门市思明区镇海路55号"
    assert reply_payload["current_message"] == normalized
    assert reply_payload["location_card"]["coordinates"] == "24.451232,118.088724"


def test_location_card_direct_reply_is_normalized_to_store_lookup() -> None:
    state = {
        "normalized_content": "定位卡片：萤火虫大厦",
        "request_context": {
            "msgtype": "location",
            "location_title": "萤火虫大厦",
            "location_address": "福建省厦门市湖里区岐山北二路1000号",
            "location": "24.535414,118.152077",
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "confirm_store",
            "reply_messages": [
                {"type": "text", "content": {"text": "我给您匹配厦门百星湖里店。"}}
            ],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_reply_messages"] == []
    assert plan["planner_tool_calls"] == [
        {
            "name": "customer_store_lookup",
            "purpose": "nearby_candidates",
            "query": "福建省厦门市湖里区岐山北二路1000号",
        }
    ]
