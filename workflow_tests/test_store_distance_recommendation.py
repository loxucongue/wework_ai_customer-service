from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "ai_paths"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.graph.nodes.action_module_outputs import build_planner_fact_output  # noqa: E402
from app.graph.nodes.appointment_utils import appointment_query_from_state  # noqa: E402
from app.graph.nodes.store_context import extract_city  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402


class FakePlatformClient:
    available = True

    def __init__(self) -> None:
        self.rows = [
            {
                "id": "12",
                "name": "厦门思明店",
                "city": "厦门",
                "address": "厦门市思明区厦禾路1222号国骏大厦",
                "tencent_address": "厦门市思明区厦禾路1222号国骏大厦",
                "status": 1,
                "shore_show": 1,
                "is_pause": 2,
            },
            {
                "id": "386",
                "name": "厦门百星",
                "city": "厦门",
                "address": "厦门市湖里区枋湖西路189号",
                "tencent_address": "厦门市湖里区枋湖西路189号",
                "status": 1,
                "shore_show": 1,
                "is_pause": 2,
            },
        ]

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        return self.rows

    def store_info(self, store_id: str, **_: object) -> dict[str, object]:
        for row in self.rows:
            if row["id"] == store_id:
                return {
                    "name": row["name"],
                    "tencent_address": row["tencent_address"],
                    "tencent_map_store": "https://map.example/store",
                    "parking_info": {},
                }
        return {}


def _customer_context() -> dict[str, object]:
    return {
        "platform_customer_id": "customer_1",
        "customer_add_wechat_id": "wechat_1",
        "request_context": {"user_id": "1", "corp_id": "corp", "wechat": "wx"},
    }


def _store_service() -> StoreService:
    return StoreService(platform_client=FakePlatformClient())  # type: ignore[arg-type]


class StoreDistanceRecommendationTests(unittest.TestCase):
    def test_airport_preference_does_not_create_hardcoded_recommendation(self) -> None:
        result = _store_service().search("厦门机场附近有门店吗", customer_context=_customer_context())

        self.assertTrue(result["stores"])
        self.assertTrue(result["distance_lookup_required"])
        self.assertEqual(result["distance_origin"], "厦门机场")
        self.assertNotIn("recommended_store", result)
        self.assertNotIn("recommendation_reason", result)

    def test_fact_output_exposes_distance_lookup_requirement(self) -> None:
        store_lookup = _store_service().search("厦门机场附近有门店吗", customer_context=_customer_context())
        fact_output = build_planner_fact_output({"store_lookup": store_lookup}, {})
        structured = fact_output["structured_facts"]

        self.assertTrue(structured["store_lookup_status"]["distance_lookup_required"])
        self.assertEqual(structured["store_lookup_status"]["distance_origin"], "厦门机场")
        self.assertEqual(structured["recommended_store"], {})
        self.assertTrue(
            any("distance_lookup_required=厦门机场" in fact for fact in fact_output["facts"]),
            fact_output["facts"],
        )

    def test_appointment_does_not_use_first_candidate_when_distance_is_required(self) -> None:
        store_lookup = _store_service().search("厦门机场附近下午想去", customer_context=_customer_context())
        query = appointment_query_from_state("厦门机场附近下午想去", store_lookup, {}, extract_city)

        self.assertIn("store_id", query["missing"])
        self.assertEqual(query["store_id"], "")
        self.assertEqual(query["store_name"], "")


if __name__ == "__main__":
    unittest.main()
