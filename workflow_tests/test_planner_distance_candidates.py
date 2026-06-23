from __future__ import annotations

import unittest

from app.graph.planner.brain_v2 import _is_distance_request_turn
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


class PlannerDistanceCandidateTests(unittest.TestCase):
    def test_distance_candidate_expands_to_city_scope(self) -> None:
        state = {
            "normalized_content": "重庆巴南附近哪家店最近",
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "189", "store_name": "重庆巴南店", "city": "重庆市", "district": "巴南区"},
                    {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                    {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    {"store_id": "11", "store_name": "上海徐汇店", "city": "上海市", "district": "徐汇区"},
                ]
            },
        }
        payload = {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "reply_messages": [{"type": "text", "content": {"text": "我帮您核对一下附近门店。"}}],
            "tool_calls": [{"name": "distance_calculate", "origin": "重庆巴南", "candidate_store_ids": ["189"]}],
            "handoff": {"needed": False, "reason": ""},
        }

        plan = build_planner_plan_v2(state, payload)
        tool = plan["planner_tool_calls"][0]

        self.assertEqual(tool["name"], "distance_calculate")
        self.assertEqual(tool["candidate_city"], "重庆市")
        self.assertEqual(tool["candidate_store_ids"], ["189", "467", "488"])
        self.assertEqual(tool["candidate_expanded_from"], ["189"])

    def test_distance_request_turn_detects_nearest_store_question(self) -> None:
        self.assertTrue(_is_distance_request_turn({"normalized_content": "重庆巴南附近哪家店最近"}))


if __name__ == "__main__":
    unittest.main()
