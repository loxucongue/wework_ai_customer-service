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


class StoreDistanceRecommendationTests(unittest.TestCase):
    def test_airport_preference_does_not_create_hardcoded_recommendation(self) -> None:
        result = StoreService().search("厦门机场附近有门店吗")

        self.assertTrue(result["stores"])
        self.assertTrue(result["distance_lookup_required"])
        self.assertEqual(result["distance_origin"], "机场附近")
        self.assertNotIn("recommended_store", result)
        self.assertNotIn("recommendation_reason", result)

    def test_fact_output_exposes_distance_lookup_requirement(self) -> None:
        store_lookup = StoreService().search("厦门机场附近有门店吗")
        fact_output = build_planner_fact_output({"store_lookup": store_lookup}, {})
        structured = fact_output["structured_facts"]

        self.assertTrue(structured["store_lookup_status"]["distance_lookup_required"])
        self.assertEqual(structured["store_lookup_status"]["distance_origin"], "机场附近")
        self.assertEqual(structured["recommended_store"], {})
        self.assertTrue(
            any("distance_lookup_required=机场附近" in fact for fact in fact_output["facts"]),
            fact_output["facts"],
        )

    def test_appointment_does_not_use_first_candidate_when_distance_is_required(self) -> None:
        store_lookup = StoreService().search("厦门机场附近下午想去")
        query = appointment_query_from_state("厦门机场附近下午想去", store_lookup, {}, extract_city)

        self.assertIn("store_id", query["missing"])
        self.assertEqual(query["store_id"], "")
        self.assertEqual(query["store_name"], "")


if __name__ == "__main__":
    unittest.main()
