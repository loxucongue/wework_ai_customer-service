from __future__ import annotations

import asyncio
import unittest

from app.graph.nodes.action_nodes import (
    _administrative_area_origin_candidate,
    _distance_candidate_stores,
    _distance_between_points,
    _geocode_has_unconflicted_location,
    _normalize_distance_origin_from_store_regions,
    _store_lookup_item,
    _stores_for_text_query,
)


class DistanceOriginNormalizationTests(unittest.TestCase):
    def test_distance_workflow_result_is_parsed_as_km(self) -> None:
        class FakeCozeClient:
            async def run_workflow(self, workflow_id: str, payload: dict) -> dict:
                self.workflow_id = workflow_id
                self.payload = payload
                return {"data": '{"output":{"distance":5241,"duration":1711}}'}

        client = FakeCozeClient()
        result = asyncio.run(_distance_between_points(client, "distance-workflow", "106.540603,29.402348", "106.545937,29.392449"))

        self.assertEqual(client.workflow_id, "distance-workflow")
        self.assertEqual(client.payload, {"origin": "106.540603,29.402348", "destination": "106.545937,29.392449"})
        self.assertEqual(result["source"], "distance_workflow")
        self.assertEqual(result["distance_meters"], 5241)
        self.assertEqual(result["distance_km"], 5.24)
        self.assertEqual(result["duration_seconds"], 1711)

    def test_city_and_district_short_name_use_customer_store_region(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "528",
                        "store_name": "重庆江北店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "江北区",
                    },
                    {
                        "store_id": "189",
                        "store_name": "重庆巴南店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "巴南区",
                    },
                ]
            }
        }

        self.assertEqual(_normalize_distance_origin_from_store_regions("重庆江北附近", state), "重庆市江北区")

    def test_ambiguous_district_keeps_original_text(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {"province": "重庆市", "city": "重庆市", "district": "江北区"},
                    {"province": "江苏省", "city": "南京市", "district": "江北区"},
                ]
            }
        }

        self.assertEqual(_normalize_distance_origin_from_store_regions("江北附近", state), "江北附近")

    def test_city_area_without_scope_district_builds_admin_candidate(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "189",
                        "store_name": "重庆巴南店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "巴南区",
                    }
                ]
            }
        }

        self.assertEqual(
            _administrative_area_origin_candidate("重庆江北附近哪家近", state),
            {"origin": "重庆市江北区", "area": "江北"},
        )

    def test_landmark_does_not_build_admin_area_candidate(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "227",
                        "store_name": "厦门湖里店",
                        "province": "福建省",
                        "city": "厦门市",
                        "district": "湖里区",
                    }
                ]
            }
        }

        self.assertEqual(_administrative_area_origin_candidate("厦门机场附近哪家近", state), {})

    def test_geocode_with_location_and_empty_district_is_unconflicted(self) -> None:
        self.assertTrue(_geocode_has_unconflicted_location({"location": "106.551787,29.562680", "district": ""}))
        self.assertFalse(_geocode_has_unconflicted_location({"location": "107.371860,29.739957", "district": "涪陵区"}))

    def test_store_lookup_city_candidates_feed_distance_source(self) -> None:
        stores = [
            {
                "store_id": "227",
                "store_name": "厦门湖里店",
                "province": "福建省",
                "city": "厦门市",
                "district": "湖里区",
                "store_address": "厦门市湖里区",
            },
            {
                "store_id": "386",
                "store_name": "厦门思明店",
                "province": "福建省",
                "city": "厦门市",
                "district": "思明区",
                "store_address": "厦门市思明区",
            },
            {
                "store_id": "467",
                "store_name": "重庆渝中店",
                "province": "重庆市",
                "city": "重庆市",
                "district": "渝中区",
                "store_address": "重庆市渝中区",
            },
        ]
        lookup_candidates = [_store_lookup_item(store) for store in _stores_for_text_query("厦门机场", stores, "nearby_candidates")]
        candidates = _distance_candidate_stores(
            {"name": "distance_calculate", "candidate_source": "customer_store_lookup"},
            {"customer_store_knowledge": {"stores": stores}},
            {"customer_store_lookup": {"candidate_stores": lookup_candidates}},
        )

        self.assertEqual([item["store_id"] for item in candidates], ["227", "386"])


if __name__ == "__main__":
    unittest.main()
