from __future__ import annotations

from app.graph.nodes.location_card import append_location_card_to_content
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
    assert reply_payload["content"] == normalized
    assert reply_payload["location_card"]["coordinates"] == "24.451232,118.088724"
