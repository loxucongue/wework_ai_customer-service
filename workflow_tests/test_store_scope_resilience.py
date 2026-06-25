from __future__ import annotations

import time

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService


class _FlakyPlatformClient:
    available = True

    def __init__(self) -> None:
        self.fail = False

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        if self.fail:
            raise TimeoutError("store index timeout")
        return [{"id": "101"}]


class _Snapshot:
    def stores_for_scope(self, rows: list[dict[str, object]], **_: object) -> dict[str, object]:
        stores = [
            {
                "store_id": str(row.get("id") or row.get("store_id") or ""),
                "store_name": "Test Store",
                "province": "Test Province",
                "city": "Test City",
                "district": "Test District",
                "store_address": "Test Address",
            }
            for row in rows
        ]
        return {
            "stores": stores,
            "grouped_by_region": {},
            "missing_snapshot_store_ids": [],
            "snapshot_generated_at": "2026-06-25T00:00:00Z",
            "snapshot_store_count": len(stores),
            "snapshot_source": "test",
            "snapshot_refresh_error": "",
        }


def test_store_scope_uses_stale_cache_when_platform_store_index_fails() -> None:
    platform = _FlakyPlatformClient()
    service = CustomerStoreKnowledgeService(platform, _Snapshot())  # type: ignore[arg-type]
    request_context = {
        "corp_id": "corp",
        "customer_id": "input-id",
        "user_id": "u1",
        "wechat": "w1",
    }
    customer_context = {"identity": {"platform_customer_id": "p1", "customer_add_wechat_id": "a1"}}

    first = service.load(request_context=request_context, customer_context=customer_context)
    assert first["store_count"] == 1
    assert first["cache"]["store_scope_status"] == "refreshed"

    key = service._scope_cache_key("p1", "a1", {**request_context, "input_customer_id": "input-id", "platform_customer_id": "p1", "customer_id": "p1", "customer_add_wechat_id": "a1"})
    service._scope_ids_cache[key] = (time.monotonic() - 1, ["101"])
    platform.fail = True

    second = service.load(request_context=request_context, customer_context=customer_context)
    assert second["store_count"] == 1
    assert second["source"] == "platform_agent.store_index_stale_cache+store_snapshot"
    assert second["cache"]["store_scope_status"] == "stale_on_error"
    assert "store index timeout" in second["store_scope_error"]


def test_store_tool_facts_keep_detail_fields_for_reply_model() -> None:
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "query": "Test City",
                "stores": [
                    {
                        "store_id": "101",
                        "store_name": "Test Store",
                        "province": "Test Province",
                        "city": "Test City",
                        "district": "Test District",
                        "store_address": "Test Address",
                        "business_hours": "09:00-19:00",
                        "parking_name": "Test Parking",
                        "parking_address": "Parking Address",
                        "parking_url": "https://example.com/parking",
                        "map_url": "https://example.com/map",
                        "location": "118.1,24.5",
                    }
                ],
            }
        },
        {},
    )
    store = output["structured_facts"]["store_facts"][0]
    assert store["store_id"] == "101"
    assert store["city"] == "Test City"
    assert store["district"] == "Test District"
    assert store["parking_name"] == "Test Parking"
    assert store["parking_address"] == "Parking Address"
    assert store["parking_url"] == "https://example.com/parking"
    assert store["map_url"] == "https://example.com/map"
    assert store["location"] == "118.1,24.5"
