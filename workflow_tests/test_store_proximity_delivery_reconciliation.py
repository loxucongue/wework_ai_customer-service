from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.graph.nodes.action_nodes import _customer_store_lookup, _geocode_query_consistency
from app.graph.nodes.reply_nodes import _apply_runtime_delivery_availability


class _GeocodeClient:
    settings = SimpleNamespace(geocode_workflow_id="geo")

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        return {
            "data": {
                "formatted_address": "上海市杨浦区长阳路",
                "province": "上海市",
                "city": "上海市",
                "district": "杨浦区",
                "location": "121.530491,31.267602",
            }
        }


def test_proximity_modifier_does_not_create_false_geocode_conflict() -> None:
    result = _geocode_query_consistency(
        "上海市杨浦区，靠近长阳路",
        {
            "province": "上海市",
            "city": "上海市",
            "district": "杨浦区",
            "formatted_address": "上海市杨浦区长阳路",
            "location": "121.530491,31.267602",
        },
    )

    assert result["status"] == "consistent"
    assert result["matched_fragments"] == ["上海市杨浦区", "长阳路"]


def test_cross_city_fragments_remain_a_geocode_conflict() -> None:
    result = _geocode_query_consistency(
        "广州市，惠州市",
        {
            "province": "广东省",
            "city": "广州市",
            "district": "天河区",
            "formatted_address": "广东省广州市天河区",
            "location": "113.3612,23.1246",
        },
    )

    assert result["status"] == "conflict"
    assert result["unresolved_fragments"] == ["惠州市"]


def test_production_shanghai_proximity_query_returns_yangpu_store() -> None:
    store = {
        "store_id": "267",
        "store_name": "上海杨浦店",
        "province": "上海市",
        "city": "上海市",
        "district": "杨浦区",
        "store_address": "上海杨浦区政通路177号万达广场E座",
        "is_open": True,
        "location": "121.513481,31.302529",
        "store_fact_integrity": "valid",
        "store_fact_integrity_violations": [],
    }
    query = "上海市杨浦区，靠近长阳路"

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "confirmed_region",
            },
            {
                "content": query,
                "normalized_content": query,
                "customer_store_knowledge": {"stores": [store]},
                "guardrail_result": {},
            },
            _GeocodeClient(),  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["location_evidence"]["confirmation_required_before_match"] is False
    assert [item["store_id"] for item in result["stores"]] == ["267"]


def test_missing_store_candidate_removes_pre_tool_store_card_requirement() -> None:
    state = {
        "reply_contract": {
            "required_deliveries": [
                {"message_type": "text"},
                {"message_type": "store_address"},
            ]
        },
        "sales_progression": {
            "required_message_types": ["text", "store_address"],
        },
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "need_location_confirmation",
                    "delivery_store_ids": [],
                }
            }
        },
    }

    adjusted, changed = _apply_runtime_delivery_availability(state)

    assert changed is True
    assert adjusted["reply_contract"]["required_deliveries"] == [{"message_type": "text"}]
    assert adjusted["sales_progression"]["required_message_types"] == ["text"]
    assert adjusted["reply_strategy"]["runtime_delivery_adjustments"] == [
        "store_lookup_has_no_deliverable_candidate"
    ]
